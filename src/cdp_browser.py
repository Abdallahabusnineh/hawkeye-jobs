"""
cdp_browser.py — Drive Chrome directly over its DevTools protocol (no chromedriver)
==================================================================================
A fallback for browser.create_driver(). The normal path, undetected-chromedriver,
downloads chromedriver and patches it; on Windows 11 with Smart App Control that
patched file is blocked ("An Application Control policy has blocked this file"),
and so is anything else unsigned. Google Chrome itself is signed, so this module
starts Chrome with --remote-debugging-port and talks to it over a local WebSocket
(websocket-client, already a Selenium dependency): no extra executable at all.

CdpDriver offers exactly the part of Selenium's WebDriver the scrapers use: get,
page_source, title, current_url, execute_script, window_handles, switch_to.window,
close, quit, set_page_load_timeout, plus wait_css() for browser.wait_for(). It
never calls Runtime.enable / Page.enable (it only evaluates and navigates), and
Chrome is started without --enable-automation, so navigator.webdriver stays false.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

log = logging.getLogger(__name__)

_START_TIMEOUT = 20


class CdpError(RuntimeError):
    """Chrome refused a command, a page failed to load, or Chrome is gone."""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_json(url: str, method: str = "GET", timeout: float = 5):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # local only, never a proxy
    with opener.open(urllib.request.Request(url, method=method), timeout=timeout) as response:
        body = response.read().decode("utf-8") or "null"
    try:
        return json.loads(body)
    except ValueError:
        return body


class _SwitchTo:
    def __init__(self, driver):
        self._driver = driver

    def window(self, handle):
        self._driver._attach(handle)


class CdpDriver:
    def __init__(self, chrome_path: str, user_data_dir=None, headless: bool = False):
        self._own_profile = user_data_dir is None
        self.user_data_dir = str(user_data_dir or tempfile.mkdtemp(prefix="hawkeye-chrome-"))
        os.makedirs(self.user_data_dir, exist_ok=True)
        self.port = _free_port()
        self._page_timeout = 30.0
        self._ws = None
        self._target = None
        self._next_id = 0
        self.switch_to = _SwitchTo(self)
        args = [chrome_path, f"--remote-debugging-port={self.port}", f"--user-data-dir={self.user_data_dir}",
                "--no-first-run", "--no-default-browser-check", "--window-size=1920,1080", "--lang=en-US",
                "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                "--disable-background-timer-throttling",
                "about:blank"]
        if headless:
            args.insert(1, "--headless=new")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      creationflags=flags)
        try:
            self._wait_until_ready()
            self._attach(self.window_handles[0])
        except Exception:
            self.quit()
            raise

    # ── connection ──
    def _base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + _START_TIMEOUT
        while True:
            if self._proc.poll() is not None:
                raise CdpError(f"Chrome exited at start (code {self._proc.returncode})")
            try:
                _http_json(self._base() + "/json/version", timeout=1)
                if self._pages():
                    return
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise CdpError("Chrome did not open its debugging port")
            time.sleep(0.2)

    def _pages(self) -> list:
        return [t for t in _http_json(self._base() + "/json/list") if t.get("type") == "page"]

    def _attach(self, target_id: str) -> None:
        import websocket
        target = next((t for t in self._pages() if t["id"] == target_id), None)
        if target is None:
            raise CdpError(f"no such window: {target_id}")
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
        # suppress_origin: Chrome refuses WebSocket clients that send an Origin header it does not allow.
        self._ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=self._page_timeout,
                                               suppress_origin=True, enable_multithread=True)
        self._target = target_id

    def _send(self, method: str, params=None, timeout=None) -> dict:
        if self._ws is None:
            raise CdpError("not connected to Chrome")
        self._next_id += 1
        msg_id = self._next_id
        self._ws.settimeout(timeout or self._page_timeout)
        try:
            self._ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
            while True:
                reply = json.loads(self._ws.recv())
                if reply.get("id") == msg_id:
                    break  # anything else is an event we did not subscribe to; ignore it
        except Exception as exc:
            raise CdpError(f"{method}: {exc}") from exc
        if "error" in reply:
            raise CdpError(f"{method}: {reply['error'].get('message')}")
        return reply.get("result", {})

    def _eval(self, expression: str, timeout=None):
        result = self._send("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                                 "awaitPromise": True}, timeout=timeout)
        if result.get("exceptionDetails"):
            raise CdpError(f"script error: {result['exceptionDetails'].get('text')}")
        return result.get("result", {}).get("value")

    # ── the WebDriver subset the scrapers use ──
    def set_page_load_timeout(self, seconds) -> None:
        self._page_timeout = float(seconds)

    def get(self, url: str) -> None:
        result = self._send("Page.navigate", {"url": url})
        if result.get("errorText"):
            raise CdpError(f"could not load {url}: {result['errorText']}")
        deadline = time.monotonic() + self._page_timeout
        time.sleep(0.3)  # let the old document start unloading before polling readyState
        while time.monotonic() < deadline:
            try:
                if self._eval("document.readyState", timeout=5) in ("interactive", "complete"):
                    return
            except CdpError:
                pass  # the context is being replaced mid-navigation
            time.sleep(0.25)
        raise CdpError(f"timed out loading {url}")

    @property
    def page_source(self) -> str:
        return self._eval("document.documentElement ? document.documentElement.outerHTML : ''") or ""

    @property
    def title(self) -> str:
        return self._eval("document.title") or ""

    @property
    def current_url(self) -> str:
        return self._eval("location.href") or ""

    def execute_script(self, script: str, *args):
        if args:
            raise NotImplementedError("CdpDriver.execute_script takes no arguments")
        return self._eval(f"(function(){{ {script} }})()")

    @property
    def window_handles(self) -> list:
        if self._proc.poll() is not None:
            raise CdpError("Chrome is not running")
        return [t["id"] for t in self._pages()]

    def wait_css(self, css: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        probe = f"document.querySelector({json.dumps(css)}) !== null"
        while time.monotonic() < deadline:
            try:
                if self._eval(probe, timeout=5):
                    return True
            except CdpError:
                pass
            time.sleep(0.25)
        return False

    def close(self) -> None:
        """Close the current tab (Selenium's close())."""
        target = self._target
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        if target:
            try:
                _http_json(f"{self._base()}/json/close/{target}")
            except OSError:
                pass

    def quit(self) -> None:
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass
        self._ws = None
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._own_profile:
            shutil.rmtree(self.user_data_dir, ignore_errors=True)
