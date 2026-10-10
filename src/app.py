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

import uvicorn

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
    manager = RunManager(notifier=DesktopNotifier(lambda: load_app_settings()["language"]))
    auto = AutoSearch(manager)
    fastapi_app = create_app(token=token, allowed_hosts={f"127.0.0.1:{port}", f"localhost:{port}"},
                             services=AppServices.default(manager))
    server = uvicorn.Server(uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="warning",
                                           access_log=False, lifespan="off"))
    return AppParts(token=token, port=port, manager=manager, auto=auto, server=server, fastapi_app=fastapi_app)


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
    """Keeps the window open while a running search stops, so the user sees «Stopping…» instead of a
    window that vanished while Chrome is still closing. A second close request closes at once
    (shutdown() still waits for the search in the background)."""

    def __init__(self, parts, window, wait_seconds: float = SHUTDOWN_WAIT_SECONDS):
        self.parts = parts
        self.window = window
        self.wait_seconds = wait_seconds
        self.stopping = False

    def on_closing(self) -> bool:
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
                   "google_jobs", "linkedin_posts"):
        try:
            __import__(module)
        except Exception as exc:
            problems.append(f"import {module}: {exc!r}")
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
    _prepare_data()
    parts = build(port=args.port)
    thread = None
    try:
        thread = _serve(parts)
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
            import webview
            window = webview.create_window(WINDOW_TITLE, url, width=1200, height=800, min_size=(360, 600))
            window.events.closing += CloseGuard(parts, window).on_closing
            webview.start()
    except KeyboardInterrupt:
        pass
    finally:
        shutdown(parts)
        if thread is not None:
            thread.join(timeout=5)


def _parse(argv):
    parser = argparse.ArgumentParser(prog="app.py", description="Hawkeye Jobs desktop app")
    parser.add_argument("--no-window", action="store_true", help="serve only and print the URL")
    parser.add_argument("--port", type=int, default=None, help="fixed port (development)")
    parser.add_argument("--self-test", action="store_true",
                        help="check that the bundled libraries load, then exit (0 = ok)")
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
