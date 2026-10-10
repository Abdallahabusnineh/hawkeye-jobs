import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

import app


class AppTests(unittest.TestCase):
    def test_free_port_is_bindable_on_localhost(self):
        port = app.free_port()
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))

    def test_window_url_carries_token(self):
        self.assertEqual(app.window_url(5000, "abc"), "http://127.0.0.1:5000/?token=abc")

    def test_build_wires_guarded_app(self):
        parts = app.build(port=5555)
        self.assertGreaterEqual(len(parts.token), 32)
        self.assertEqual(parts.server.config.host, "127.0.0.1")
        self.assertEqual(parts.server.config.port, 5555)
        self.assertFalse(parts.server.config.access_log)
        from fastapi.testclient import TestClient
        client = TestClient(parts.fastapi_app, base_url="http://127.0.0.1:5555")
        self.assertEqual(client.get("/api/state").status_code, 403)
        self.assertEqual(client.get("/api/state", headers={"X-Hawkeye-Token": parts.token}).status_code, 200)

    def test_shutdown_order_is_pinned(self):
        parts = MagicMock()
        parts.manager.wait.return_value = True
        app.shutdown(parts, wait_seconds=1)
        names = [c[0] for c in parts.mock_calls if c[0] in (
            "auto.stop", "manager.close", "manager.cancel", "manager.wait", "manager.close_subscribers")]
        self.assertEqual(names, ["auto.stop", "manager.close", "manager.cancel", "manager.wait",
                                 "manager.close_subscribers"])
        parts.manager.wait.assert_called_once_with(1)
        self.assertTrue(parts.server.should_exit)

    def test_shutdown_stops_a_real_running_search_and_refuses_new_ones(self):
        import tempfile
        import threading
        from runner import RunManager
        from run_lock import RunLock
        from store import Store
        from types import SimpleNamespace

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        started = threading.Event()

        def slow_search(settings, **kw):
            cancel = kw["cancel"]
            started.set()
            cancel.wait(10)
            return SimpleNamespace(run_id=None, state="cancelled", found=0, new_jobs=[])

        manager = RunManager(notifier=lambda jobs: True, run_search=slow_search,
                             store_factory=lambda: Store(Path(tmp.name) / "t.db"),
                             lock=RunLock(Path(tmp.name) / "run.lock"),
                             settings_loader=lambda: {"keywords": ["x"], "sources": ["bayt"], "locations": []},
                             app_settings_loader=lambda: {"last_posts_run": None, "language": "en"},
                             country_labels_loader=lambda: {})
        threading.Thread(target=manager.start, daemon=True).start()
        self.assertTrue(started.wait(5))
        parts = SimpleNamespace(manager=manager, auto=MagicMock(), server=SimpleNamespace(should_exit=False))
        app.shutdown(parts, wait_seconds=5)
        self.assertFalse(manager.is_running)
        from runner import RunInProgress
        with self.assertRaises(RunInProgress):
            manager.start()
        self.assertTrue(parts.server.should_exit)

    def test_main_survives_report_failure_and_still_shuts_down(self):
        from unittest.mock import patch
        parts = MagicMock()
        parts.manager.report_pending_now.side_effect = RuntimeError("db locked")
        thread = MagicMock()
        thread.is_alive.return_value = False
        with patch.object(app, "setup_logging"), patch.object(app, "_prepare_data"), \
                patch.object(app, "build", return_value=parts), patch.object(app, "_serve", return_value=thread), \
                patch.object(app, "shutdown") as shut, self.assertLogs("app", "ERROR"), \
                patch("builtins.print"), patch("sys.stderr"):
            with self.assertRaises(SystemExit) as ctx:
                app.main(["--no-window"])
        self.assertEqual(ctx.exception.code, 1)  # server thread was dead
        parts.manager.report_pending_now.assert_called_once()
        shut.assert_called_once_with(parts)


if __name__ == "__main__":
    unittest.main()
