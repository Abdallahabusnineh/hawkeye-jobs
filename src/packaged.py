"""
packaged.py — Running as the Microsoft Store app (an MSIX package)
==================================================================
The same build ships two ways on Windows: the installer / zip from GitHub, and an
MSIX package from the Microsoft Store (which Microsoft signs, so Smart App Control
lets it run). Inside a package a few things work differently:

- Data folder: writes to %LOCALAPPDATA% are silently redirected into the package,
  so the path the app sees is not the one File Explorer opens. The packaged app
  uses its own real folder instead, %LOCALAPPDATA%\\Packages\\<family>\\LocalState
  (paths.py), which Windows also removes when the app is uninstalled.
- Start at login: the Run key is redirected too; a package declares a StartupTask
  (packaging/windows/msix/AppxManifest.xml) that the app turns on and off through
  WinRT. A StartupTask cannot pass --background, so the app asks how it was started.
- Updates come from the Store, not from GitHub releases.

Everything here answers "not packaged" off Windows or outside a package.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)

STORE_PRODUCT_ID = "9PB9XTPR0XH4"
STORE_URL = f"https://apps.microsoft.com/detail/{STORE_PRODUCT_ID}"
STARTUP_TASK_ID = "HawkeyeJobsAtLogin"  # must match the StartupTask TaskId in AppxManifest.xml

_APPMODEL_ERROR_NO_PACKAGE = 15700
_ERROR_INSUFFICIENT_BUFFER = 122


@functools.lru_cache(maxsize=1)
def package_family_name() -> str | None:
    """This process's package family name, or None when it is not running from a package."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        get_name = ctypes.windll.kernel32.GetCurrentPackageFamilyName
        get_name.argtypes = [ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR]
        get_name.restype = wintypes.LONG
        length = wintypes.UINT(0)
        result = get_name(ctypes.byref(length), None)
        if result != _ERROR_INSUFFICIENT_BUFFER:
            return None  # usually APPMODEL_ERROR_NO_PACKAGE: a normal install
        buffer = ctypes.create_unicode_buffer(length.value)
        if get_name(ctypes.byref(length), buffer) != 0:
            return None
        return buffer.value or None
    except Exception:
        log.warning("Could not ask Windows whether this is a packaged app", exc_info=True)
        return None


def is_packaged() -> bool:
    return package_family_name() is not None


def local_state_dir(family: str, local_app_data: str | None = None) -> Path:
    """The package's own data folder: real on disk, visible in File Explorer, removed at uninstall."""
    base = local_app_data or os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Packages" / family / "LocalState"


def _run_async(make_awaitable):
    """Run a WinRT async call to completion on a fresh thread (callers may already be inside an
    event loop, e.g. the notifier's)."""
    box = {}

    async def wait():
        return await make_awaitable()

    def target():
        try:
            box["value"] = asyncio.run(wait())
        except BaseException as exc:  # handed back to the caller below
            box["error"] = exc

    thread = threading.Thread(target=target, name="hawkeye-winrt", daemon=True)
    thread.start()
    thread.join(30)
    if thread.is_alive():
        raise TimeoutError("Windows did not answer")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def launched_at_login() -> bool:
    """True when Windows started the packaged app through its StartupTask (at sign-in)."""
    if not is_packaged():
        return False
    try:
        from winrt.windows.applicationmodel import AppInstance
        from winrt.windows.applicationmodel.activation import ActivationKind
        event = AppInstance.get_activated_event_args()
        return event is not None and event.kind == ActivationKind.STARTUP_TASK
    except Exception:
        log.warning("Could not read how the app was started", exc_info=True)
        return False


class StartupTaskBlocked(RuntimeError):
    """The user turned the app off in Windows Settings > Apps > Startup (or a policy did): only
    they can turn it back on, there."""


class StoreStartupTask:
    """Start at login for the packaged app, through its manifest's StartupTask."""

    def __init__(self, task_id: str = STARTUP_TASK_ID):
        self.task_id = task_id

    def _task(self):
        from winrt.windows.applicationmodel import StartupTask
        return _run_async(lambda: StartupTask.get_async(self.task_id))

    @staticmethod
    def _states():
        from winrt.windows.applicationmodel import StartupTaskState
        return StartupTaskState

    def is_enabled(self) -> bool:
        try:
            states = self._states()
            return self._task().state in (states.ENABLED, states.ENABLED_BY_POLICY)
        except Exception:
            log.warning("Could not read the startup task", exc_info=True)
            return False

    def enable(self) -> None:
        states = self._states()
        task = self._task()
        if task.state in (states.ENABLED, states.ENABLED_BY_POLICY):
            return
        if task.state in (states.DISABLED_BY_USER, states.DISABLED_BY_POLICY):
            raise StartupTaskBlocked(str(task.state))
        state = _run_async(task.request_enable_async)
        if state not in (states.ENABLED, states.ENABLED_BY_POLICY):
            raise StartupTaskBlocked(str(state))

    def disable(self) -> None:
        task = self._task()
        task.disable()
