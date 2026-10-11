#!/usr/bin/env python3
"""
app.py — Hawkeye Jobs desktop app
==================================
Starts the local web server (127.0.0.1, random port, per-launch token) on a
background thread and opens a native window (pywebview) on it. Everything
stays on the user's machine. Closing the window stops any running search
gracefully (in-flight requests finish, Chrome closes), then exits.

    python src/app.py               # open the app window
    python src/app.py --no-window   # serve only and print the URL (UI development)
"""

import argparse
import logging
import os
import secrets
import socket
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

FROZEN = getattr(sys, "frozen", False)  # running from the packaged app (PyInstaller)
if FROZEN:
    # A windowed app has no console: give libraries that write to it (uvicorn) somewhere harmless.
    for _name in ("stdout", "stderr"):
        if getattr(sys, _name) is None:
            setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))
    # The bundled Python has no system certificate list, so urllib (undetected-chromedriver
    # downloading chromedriver) failed every HTTPS request. Use certifi's list, as requests does.
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except ImportError:
        pass

import uvicorn

from background import Autostart, SingleInstance
from logs import setup_logging
from notify import DesktopNotifier
from runner import AutoSearch, RunManager
from settings import load_app_settings, migrate_legacy_config
from store import Store
from web.server import create_app
from web.services import AppServices

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
WINDOW_TITLE = "Hawkeye Jobs"
SHUTDOWN_WAIT_SECONDS = 35


@dataclass
class AppParts:
    token: str
    port: int
    manager: RunManager
    auto: AutoSearch
    server: uvicorn.Server
    fastapi_app: object
    services: object = None
    notifier: object = None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def window_url(port: int, token: str) -> str:
    return f"http://127.0.0.1:{port}/?token={token}"


def _prepare_data() -> None:
    # The packaged app has no repo folder holding a terminal-era config.json / seen_jobs.json.
    if not FROZEN:
        migrate_legacy_config(ROOT)
    with Store() as store:
        if not FROZEN:
            store.import_legacy_seen_file(ROOT / "seen_jobs.json")


def build(port=None) -> AppParts:
    port = port or free_port()
    token = secrets.token_urlsafe(32)
    notifier = DesktopNotifier(lambda: load_app_settings()["language"])
    manager = RunManager(notifier=notifier)
    auto = AutoSearch(manager)
    services = AppServices.default(manager)
    autostart = Autostart()
    services.set_start_at_login = autostart.apply
    services.background = lambda: {"tray": False, "autostart": autostart.supported}
    fastapi_app = create_app(token=token, allowed_hosts={f"127.0.0.1:{port}", f"localhost:{port}"},
                             services=services)
    server = uvicorn.Server(uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="warning",
                                           access_log=False, lifespan="off"))
    return AppParts(token=token, port=port, manager=manager, auto=auto, server=server, fastapi_app=fastapi_app,
                    services=services, notifier=notifier)


def _serve(parts: AppParts) -> threading.Thread:
    thread = threading.Thread(target=parts.server.run, name="hawkeye-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not parts.server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("the local server did not start")
        time.sleep(0.05)
    return thread


def shutdown(parts, wait_seconds: float = SHUTDOWN_WAIT_SECONDS) -> None:
    """Stop the scheduler, refuse new searches, stop the running one gracefully, then the streams and server."""
    parts.auto.stop()
    parts.manager.close()
    parts.manager.cancel()
    if not parts.manager.wait(wait_seconds):
        log.warning("A search was still running %s s after shutdown began", wait_seconds)
    parts.manager.close_subscribers()
    parts.server.should_exit = True


class CloseGuard:
    """Decides what closing the window does.

    - Keep running (the default, when a tray icon exists): hide the window; automatic search and
      notifications go on. Quit from the tray icon or the app menu (Cmd+Q) sets `quitting`.
    - Quitting: keep the window open while a running search stops, so the user sees «Stopping…»
      instead of a window that vanished while Chrome is still closing. A second close request
      closes at once (shutdown() still waits for the search in the background)."""

    def __init__(self, parts, window, wait_seconds: float = SHUTDOWN_WAIT_SECONDS,
                 keep_running=lambda: False, on_hidden=lambda: None):
        self.parts = parts
        self.window = window
        self.wait_seconds = wait_seconds
        self.keep_running = keep_running
        self.on_hidden = on_hidden
        self.stopping = False
        self.quitting = False
        self.show = lambda: None

    def quit(self) -> None:
        """Quit for real (tray «Quit», Cmd+Q). Closing the window ends webview.start(), so main()'s
        shutdown runs. A running search is stopped first, with «Stopping…» in a visible window."""
        self.quitting = True
        if self.parts.manager.is_running and not self.stopping:
            self.stopping = True
            self.show()
            threading.Thread(target=self._stop_then_close, name="hawkeye-closing", daemon=True).start()
        elif not self.stopping:
            self.window.destroy()

    def on_closing(self) -> bool:
        if not self.quitting and not self.stopping and self.keep_running():
            self.window.hide()
            try:
                self.on_hidden()
            except Exception:
                log.exception("After hiding the window")
            return False
        if self.stopping or not self.parts.manager.is_running:
            return True
        self.stopping = True
        threading.Thread(target=self._stop_then_close, name="hawkeye-closing", daemon=True).start()
        return False

    def _stop_then_close(self) -> None:
        try:
            self.window.evaluate_js("window.dispatchEvent(new Event('hawkeye-closing'))")
        except Exception:
            log.exception("Could not show the closing message")
        self.parts.auto.stop()
        self.parts.manager.close()
        self.parts.manager.cancel()
        self.parts.manager.wait(self.wait_seconds)
        self.window.destroy()


_HIDDEN_HINT = {
    ("ar", "darwin"): "ما زال Hawkeye Jobs يعمل في الخلفية ويبحث عن الوظائف. افتحه من أيقونته في شريط القوائم أعلى الشاشة.",
    ("ar", "other"): "ما زال Hawkeye Jobs يعمل في الخلفية ويبحث عن الوظائف. افتحه من أيقونته بجوار الساعة.",
    ("en", "darwin"): "Hawkeye Jobs is still running in the background and searching for jobs. Open it from its icon in the menu bar.",
    ("en", "other"): "Hawkeye Jobs is still running in the background and searching for jobs. Open it from its icon next to the clock.",
}
_TRAY_TEXT = {
    "ar": {"open": "فتح Hawkeye Jobs", "search": "ابحث الآن", "quit": "إنهاء Hawkeye Jobs"},
    "en": {"open": "Open Hawkeye Jobs", "search": "Search now", "quit": "Quit Hawkeye Jobs"},
}


def hidden_hint(language: str, platform: str = sys.platform) -> str:
    return _HIDDEN_HINT[("ar" if language == "ar" else "en", "darwin" if platform == "darwin" else "other")]


def _icon_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", "")) if FROZEN else ROOT / "packaging"
    return base / "icon.png"


def _make_tray(language, on_open, on_search, on_quit):
    """The menu-bar / notification-area icon, or None where the system has none (then closing
    the window quits, as before). Must be created on the main thread before webview.start()."""
    try:
        import pystray
        from PIL import Image
        text = _TRAY_TEXT["ar" if language == "ar" else "en"]
        menu = pystray.Menu(
            pystray.MenuItem(text["open"], lambda icon, item: on_open(), default=True),
            pystray.MenuItem(text["search"], lambda icon, item: on_search()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(text["quit"], lambda icon, item: on_quit()),
        )
        icon = pystray.Icon("hawkeye-jobs", Image.open(_icon_path()), WINDOW_TITLE, menu)
        icon.run_detached()
        return icon
    except Exception:
        log.warning("No tray icon on this system; closing the window will quit", exc_info=True)
        return None


def _in_thread(func):
    """Tray callbacks run on the GUI thread; window calls must not block it."""
    return lambda: threading.Thread(target=func, daemon=True).start()


def _mac_hooks(guard, show):
    """macOS: Cmd+Q / Quit really quits (it would otherwise just hide the window), and clicking the
    Dock icon or opening the app again from Finder shows the hidden window."""
    import objc
    from Foundation import NO, YES  # noqa: F401
    from webview.platforms.cocoa import BrowserView

    def applicationShouldTerminate_(self, app):
        # Letting AppKit terminate would end the process at once, skipping main()'s shutdown (stop the
        # search, close Chrome, release the locks). Close the window instead and refuse; the app then
        # exits through the normal path. (The quit request reports "cancelled", which nobody sees.)
        log.info("Quit requested from the app menu")
        guard.quit()
        return NO

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, has_visible):
        show()
        return YES

    objc.classAddMethods(BrowserView.AppDelegate, [
        objc.selector(applicationShouldTerminate_, selector=b"applicationShouldTerminate:",
                      signature=BrowserView.AppDelegate.applicationShouldTerminate_.signature),
        objc.selector(applicationShouldHandleReopen_hasVisibleWindows_,
                      selector=b"applicationShouldHandleReopen:hasVisibleWindows:", signature=b"Z@:@Z"),
    ])


def _mac_dock(visible: bool) -> None:
    """Show the Dock icon only while the window is open (a hidden app lives in the menu bar)."""
    if sys.platform != "darwin":
        return
    from AppKit import NSApplication
    from PyObjCTools import AppHelper
    app = NSApplication.sharedApplication()
    AppHelper.callAfter(app.setActivationPolicy_, 0 if visible else 1)
    if visible:
        AppHelper.callAfter(app.activateIgnoringOtherApps_, True)


def self_test() -> list:
    """Problems that would break a packaged build without failing at startup (missing data files,
    native libraries, lazily imported modules). Empty list = all good. Makes no network requests."""
    problems = []
    try:
        from countries import load_countries, localized_countries
        rows = load_countries()
        if len(rows) < 200:
            problems.append(f"country list has only {len(rows)} entries (Babel data missing?)")
        if {c["id"]: c["label"] for c in localized_countries("ar", rows)}.get("jo") != "الأردن":
            problems.append("Arabic country names missing (Babel data)")
    except Exception as exc:
        problems.append(f"countries: {exc!r}")
    for module in ("jobspy", "tls_client.cffi", "undetected_chromedriver", "selenium.webdriver",
                   "webview", "desktop_notifier", "pipeline", "scraper", "bayt", "naukrigulf",
                   "google_jobs", "linkedin_posts", "PIL.Image"):
        try:
            __import__(module)
        except Exception as exc:
            problems.append(f"import {module}: {exc!r}")
    # Importing pystray connects to the display (fails on a headless Linux machine, where the app
    # simply runs without a tray), so only check that it is bundled.
    import importlib.util
    if importlib.util.find_spec("pystray") is None:
        problems.append("pystray is missing (tray icon)")
    if FROZEN:
        try:
            import ssl
            if not ssl.create_default_context().cert_store_stats().get("x509_ca"):
                problems.append("no trusted certificates: urllib HTTPS (chromedriver download) would fail")
        except Exception as exc:
            problems.append(f"ssl: {exc!r}")
    if not _icon_path().is_file():
        problems.append("missing tray icon (icon.png)")
    from web.server import STATIC_DIR
    for name in ("index.html", "app.js", "i18n/ar.json", "i18n/en.json", "vendor/alpine.min.js"):
        if not (STATIC_DIR / name).is_file():
            problems.append(f"missing window file {name}")
    return problems


def main(argv=None) -> None:
    args = _parse(argv)
    if args.self_test:
        problems = self_test()
        for problem in problems:
            print(problem, file=sys.stderr)
        sys.exit(1 if problems else 0)
    setup_logging()
    instance = SingleInstance()
    if not instance.acquire():
        # Already running (perhaps hidden in the tray): bring that window up instead of a second app.
        if not args.background and not instance.ask_running_copy_to_show():
            log.warning("Another Hawkeye Jobs is running but did not answer")
        log.info("Hawkeye Jobs is already running; this launch exits")
        return
    _prepare_data()
    parts = build(port=args.port)
    thread = None
    tray = None
    try:
        thread = _serve(parts)
        instance.publish(parts.port, parts.token)
        try:
            if load_app_settings()["start_at_login"]:
                Autostart().apply(True)  # the app may have moved since the login item was written
        except Exception:
            log.exception("Could not refresh the login item")
        try:
            parts.manager.recover_interrupted()
        except Exception:
            log.exception("Recovering interrupted runs failed; it will be retried at the next launch")
        parts.auto.start()
        try:
            parts.manager.report_pending_now()
        except Exception:
            log.exception("Reporting pending jobs failed; they will be retried at the next launch")
        url = window_url(parts.port, parts.token)
        if args.no_window:
            print(f"Hawkeye Jobs is running at {url}  (Ctrl+C to stop)", flush=True)
            while thread.is_alive():
                time.sleep(0.5)
            log.error("The local server stopped unexpectedly")
            print("The local server stopped unexpectedly.", file=sys.stderr)
            sys.exit(1)
        else:
            tray = _run_window(parts, url, background=args.background)
    except KeyboardInterrupt:
        pass
    finally:
        if tray is not None:
            try:
                tray.stop()
            except Exception:
                log.exception("Could not remove the tray icon")
        shutdown(parts)
        if thread is not None:
            thread.join(timeout=5)
        instance.release()


def _run_window(parts, url, background=False):
    """Open the window (hidden with --background) and the tray icon; return when the app quits."""
    import webview
    # macOS can create the window hidden; elsewhere a hidden window never reports "shown",
    # so it starts minimized and is hidden as soon as it appears.
    hide_at_start = background and sys.platform != "darwin"
    window = webview.create_window(WINDOW_TITLE, url, width=1200, height=800, min_size=(360, 600),
                                   hidden=background and sys.platform == "darwin", minimized=hide_at_start)

    def show():
        _mac_dock(True)
        window.restore()
        window.show()

    def on_hidden():
        _mac_dock(False)
        app = load_app_settings()
        if not app.get("background_hint_shown"):
            parts.notifier.send_text(hidden_hint(app["language"]))
            from settings import save_app_settings
            save_app_settings({"background_hint_shown": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})

    def search_now():
        try:
            parts.manager.start("manual")
        except Exception as exc:  # RunInProgress, closing, …
            log.info("Search from the tray not started: %s", exc)

    # The notification must not run on the GUI thread (macOS delivers its callbacks there).
    guard = CloseGuard(parts, window, on_hidden=_in_thread(on_hidden))
    guard.show = show
    tray = _make_tray(load_app_settings()["language"], _in_thread(show), _in_thread(search_now),
                      _in_thread(guard.quit))
    guard.keep_running = lambda: tray is not None and load_app_settings()["keep_running"]
    parts.services.show_window = show
    parts.services.background = lambda: {"tray": tray is not None, "autostart": Autostart().supported}
    window.events.closing += guard.on_closing
    if sys.platform == "darwin":
        try:
            _mac_hooks(guard, _in_thread(show))
        except Exception:
            log.exception("Could not install the macOS quit/reopen handlers")

    def started():
        if background and sys.platform == "darwin":
            _mac_dock(False)
        if hide_at_start:
            window.events.shown.wait(20)
            window.hide()

    webview.start(started)
    return tray


def _parse(argv):
    parser = argparse.ArgumentParser(prog="app.py", description="Hawkeye Jobs desktop app")
    parser.add_argument("--no-window", action="store_true", help="serve only and print the URL")
    parser.add_argument("--port", type=int, default=None, help="fixed port (development)")
    parser.add_argument("--background", action="store_true",
                        help="start hidden in the tray (used by the login item)")
    parser.add_argument("--self-test", action="store_true",
                        help="check that the bundled libraries load, then exit (0 = ok)")
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
