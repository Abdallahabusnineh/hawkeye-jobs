import json
import plistlib
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

from background import APP_ID, APP_NAME, Autostart, SingleInstance

CMD = ["/Apps/Hawkeye Jobs.app/Contents/MacOS/Hawkeye Jobs", "--background"]


class FakeRegistry:
    """Just enough of winreg for the Run key."""
    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def OpenKey(self, root, path, *a):
        return self._Key()

    def CreateKey(self, root, path):
        return self._Key()

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def test_macos_launch_agent(self):
        a = Autostart(platform="darwin", home=self.home, command=CMD)
        self.assertFalse(a.is_enabled())
        a.apply(True)
        path = self.home / "Library" / "LaunchAgents" / f"{APP_ID}.plist"
        data = plistlib.loads(path.read_bytes())
        self.assertEqual(data["ProgramArguments"], CMD)
        self.assertTrue(data["RunAtLoad"])
        self.assertTrue(a.is_enabled())
        a.apply(False)
        self.assertFalse(path.exists())
        a.apply(False)  # already off: no error

    def test_windows_run_key_quotes_paths_with_spaces(self):
        reg = FakeRegistry()
        a = Autostart(platform="win32", home=self.home, registry=reg,
                      command=[r"C:\Users\Sam\AppData\Local\Programs\Hawkeye Jobs\Hawkeye Jobs.exe", "--background"])
        a.apply(True)
        self.assertEqual(reg.values[APP_NAME],
                         r'"C:\Users\Sam\AppData\Local\Programs\Hawkeye Jobs\Hawkeye Jobs.exe" --background')
        self.assertTrue(a.is_enabled())
        a.apply(False)
        self.assertFalse(a.is_enabled())
        a.apply(False)

    def test_linux_autostart_desktop_file(self):
        from unittest.mock import patch
        with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(self.home / "cfg")}):
            a = Autostart(platform="linux", home=self.home, command=["/opt/Hawkeye Jobs.AppImage", "--background"])
            a.apply(True)
            text = (self.home / "cfg" / "autostart" / "hawkeye-jobs.desktop").read_text()
            self.assertIn("Exec='/opt/Hawkeye Jobs.AppImage' --background", text)
            self.assertTrue(a.is_enabled())
            a.apply(False)
            self.assertFalse(a.is_enabled())

    def test_launch_command_from_source_runs_app_py_hidden(self):
        from background import launch_command
        cmd = launch_command()
        self.assertEqual(cmd[0], sys.executable)
        self.assertTrue(cmd[1].endswith("app.py"))
        self.assertEqual(cmd[-1], "--background")


class SingleInstanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_only_the_first_copy_runs(self):
        first, second = SingleInstance(self.tmp.name), SingleInstance(self.tmp.name)
        self.addCleanup(first.release)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        first.release()
        self.assertTrue(second.acquire())
        second.release()

    def test_second_copy_asks_the_first_to_show_its_window(self):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append((self.path, self.headers.get("X-Hawkeye-Token")))
                self.send_response(204)
                self.end_headers()

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        first = SingleInstance(self.tmp.name)
        self.assertTrue(first.acquire())
        self.addCleanup(first.release)
        first.publish(server.server_address[1], "secret")
        self.assertEqual(json.loads(first.info_path.read_text())["token"], "secret")
        self.assertTrue(SingleInstance(self.tmp.name).ask_running_copy_to_show())
        self.assertEqual(calls, [("/api/window/show", "secret")])

    def test_no_answer_is_false(self):
        self.assertFalse(SingleInstance(self.tmp.name).ask_running_copy_to_show())   # no instance.json
        (Path(self.tmp.name) / "instance.json").write_text('{"port": 1, "token": "x"}')
        self.assertFalse(SingleInstance(self.tmp.name).ask_running_copy_to_show(timeout=1))

    def test_release_removes_the_contact_file(self):
        first = SingleInstance(self.tmp.name)
        first.acquire()
        first.publish(1234, "t")
        first.release()
        self.assertFalse(first.info_path.exists())


if __name__ == "__main__":
    unittest.main()
