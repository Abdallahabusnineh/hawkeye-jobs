"""
background.py — Keep running after the window closes; start at login; one copy at a time
=======================================================================================
Automatic search only helps if the app is running, so closing the window hides it
and the app stays in the menu bar (macOS) / notification area (Windows, Linux) —
see app.py for the tray icon. This module holds the parts that do not need a GUI:

- Autostart: an OS login item that starts the app hidden (`--background`):
    macOS   ~/Library/LaunchAgents/<id>.plist   (RunAtLoad)
    Windows HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run  "Hawkeye Jobs"
    Linux   ~/.config/autostart/hawkeye-jobs.desktop
  It points at the current executable, so it is rewritten at every launch while
  enabled (the user may have moved the app).

- SingleInstance: one app per user. The first copy holds <data>/app.lock and writes
  <data>/instance.json (port + token). A second launch asks the first to show its
  window (POST /api/window/show) and exits, so opening the app from the Start menu
  or Finder while it sits in the tray brings the window back instead of starting
  a second server and scheduler.
"""

from __future__ import annotations

import json
import logging
import os
import plistlib
import shlex
import sys
from pathlib import Path

from paths import data_dir

log = logging.getLogger(__name__)

APP_ID = "io.github.abdallahabusnineh.hawkeyejobs"
APP_NAME = "Hawkeye Jobs"
BACKGROUND_FLAG = "--background"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command() -> list:
    """The command that starts this app hidden: the packaged executable (or AppImage), or
    Python + app.py when running from source."""
    if os.environ.get("APPIMAGE"):
        return [os.environ["APPIMAGE"], BACKGROUND_FLAG]
    if getattr(sys, "frozen", False):
        return [sys.executable, BACKGROUND_FLAG]
    return [sys.executable, str(Path(__file__).resolve().parent / "app.py"), BACKGROUND_FLAG]


class Autostart:
    """Start the app at login. `platform`, `home` and `registry` are injectable for tests."""

    def __init__(self, platform=None, home=None, registry=None, command=None):
        self.platform = platform or sys.platform
        self.home = Path(home) if home else Path.home()
        self._registry = registry
        self._command = command

    @property
    def supported(self) -> bool:
        return self.platform == "darwin" or self.platform == "win32" or self.platform.startswith("linux")

    def command(self) -> list:
        return list(self._command) if self._command else launch_command()

    # ── paths ──
    def _plist(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{APP_ID}.plist"

    def _desktop_file(self) -> Path:
        config = Path(os.environ.get("XDG_CONFIG_HOME") or self.home / ".config")
        return config / "autostart" / "hawkeye-jobs.desktop"

    def _winreg(self):
        if self._registry is not None:
            return self._registry
        import winreg
        return winreg

    # ── public ──
    def is_enabled(self) -> bool:
        try:
            if self.platform == "darwin":
                return self._plist().is_file()
            if self.platform == "win32":
                reg = self._winreg()
                with reg.OpenKey(reg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                    reg.QueryValueEx(key, APP_NAME)
                return True
            return self._desktop_file().is_file()
        except OSError:
            return False

    def enable(self) -> None:
        cmd = self.command()
        if self.platform == "darwin":
            path = self._plist()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "wb") as f:
                plistlib.dump({"Label": APP_ID, "ProgramArguments": cmd, "RunAtLoad": True,
                               "ProcessType": "Interactive"}, f)
        elif self.platform == "win32":
            reg = self._winreg()
            value = " ".join(f'"{part}"' if " " in part else part for part in cmd)
            with reg.CreateKey(reg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                reg.SetValueEx(key, APP_NAME, 0, reg.REG_SZ, value)
        else:
            path = self._desktop_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "[Desktop Entry]\nType=Application\nName=Hawkeye Jobs\n"
                f"Exec={shlex.join(cmd)}\nIcon=hawkeye-jobs\nTerminal=false\n"
                "X-GNOME-Autostart-enabled=true\n", encoding="utf-8")

    def disable(self) -> None:
        try:
            if self.platform == "darwin":
                self._plist().unlink(missing_ok=True)
            elif self.platform == "win32":
                reg = self._winreg()
                with reg.OpenKey(reg.HKEY_CURRENT_USER, _RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
                    reg.DeleteValue(key, APP_NAME)
            else:
                self._desktop_file().unlink(missing_ok=True)
        except FileNotFoundError:
            pass

    def apply(self, enabled: bool) -> None:
        """Make the OS match the setting. Rewrites the entry when enabled (the app may have moved)."""
        if enabled:
            self.enable()
        else:
            self.disable()


class SingleInstance:
    """One running app per user. acquire() → True for the first copy."""

    def __init__(self, folder=None):
        from run_lock import RunLock
        self.folder = Path(folder) if folder else data_dir()
        self._lock = RunLock(self.folder / "app.lock")
        self.info_path = self.folder / "instance.json"

    def acquire(self) -> bool:
        return self._lock.acquire()

    def publish(self, port: int, token: str) -> None:
        """Tell later launches how to reach this copy. Only this user can read the data folder."""
        tmp = self.info_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"port": port, "token": token}), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.info_path)

    def release(self) -> None:
        try:
            self.info_path.unlink(missing_ok=True)
        except OSError:
            pass
        self._lock.release()

    def ask_running_copy_to_show(self, timeout: float = 5) -> bool:
        """Ask the running copy to show its window. True if it answered."""
        try:
            info = json.loads(self.info_path.read_text(encoding="utf-8"))
            port, token = int(info["port"]), str(info["token"])
        except (OSError, ValueError, KeyError, TypeError):
            return False
        import urllib.request
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/window/show", method="POST",
                                         headers={"X-Hawkeye-Token": token}, data=b"")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via a proxy
        try:
            with opener.open(request, timeout=timeout) as response:
                return 200 <= response.status < 300
        except OSError:
            return False
