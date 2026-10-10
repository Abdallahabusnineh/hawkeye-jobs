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
    migrate_legacy_config(ROOT)
    with Store() as store:
        store.recover_interrupted_runs()
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


def main(argv=None) -> None:
    args = _parse(argv)
    setup_logging()
    _prepare_data()
    parts = build()
    thread = None
    try:
        thread = _serve(parts)
        parts.auto.start()
        try:
            parts.manager.report_pending_now()
        except Exception:
            log.exception("Reporting pending jobs failed; they will be retried at the next launch")
        url = window_url(parts.port, parts.token)
        if args.no_window:
            print(f"Hawkeye Jobs is running at {url}  (Ctrl+C to stop)")
            while thread.is_alive():
                time.sleep(0.5)
            log.error("The local server stopped unexpectedly")
            print("The local server stopped unexpectedly.", file=sys.stderr)
            sys.exit(1)
        else:
            import webview
            webview.create_window(WINDOW_TITLE, url, width=1200, height=800, min_size=(360, 600))
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
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
