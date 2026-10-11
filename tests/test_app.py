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

    def test_self_test_passes_from_source(self):
        self.assertEqual(app.self_test(), [])

    def test_self_test_option_exits_with_its_result(self):
        from unittest.mock import patch
        with patch.object(app, "self_test", return_value=["broken"]), \
             patch.object(app, "setup_logging") as setup, self.assertRaises(SystemExit) as stop:
            app.main(["--self-test"])
        self.assertEqual(stop.exception.code, 1)
        setup.assert_not_called()

    def test_second_launch_shows_the_running_window_and_exits(self):
        from unittest.mock import patch
        instance = MagicMock()
        instance.acquire.return_value = False
        with patch.object(app, "setup_logging"), patch.object(app, "SingleInstance", return_value=instance), \
                patch.object(app, "build") as build:
            app.main([])
        instance.ask_running_copy_to_show.assert_called_once()
        build.assert_not_called()

    def test_background_launch_while_running_exits_quietly(self):
        from unittest.mock import patch
        instance = MagicMock()
        instance.acquire.return_value = False
        with patch.object(app, "setup_logging"), patch.object(app, "SingleInstance", return_value=instance), \
                patch.object(app, "build") as build:
            app.main(["--background"])
        instance.ask_running_copy_to_show.assert_not_called()
        build.assert_not_called()

    def test_close_hides_the_window_when_keep_running(self):
        parts = MagicMock()
        parts.manager.is_running = True
        window = MagicMock()
        hidden = []
        guard = app.CloseGuard(parts, window, keep_running=lambda: True, on_hidden=lambda: hidden.append(1))
        self.assertFalse(guard.on_closing())
        window.hide.assert_called_once()
        self.assertEqual(hidden, [1])
        parts.manager.cancel.assert_not_called()   # the search goes on in the background

    def test_quit_closes_even_when_keep_running(self):
        parts = MagicMock()
        parts.manager.is_running = False
        window = MagicMock()
        guard = app.CloseGuard(parts, window, keep_running=lambda: True)
        guard.quit()
        window.destroy.assert_called_once()
        self.assertTrue(guard.on_closing())         # a close request after Quit is not turned into a hide
        window.hide.assert_not_called()

    def test_quit_during_a_search_stops_it_in_a_visible_window_then_closes(self):
        import threading
        parts = MagicMock()
        parts.manager.is_running = True
        window = MagicMock()
        closed = threading.Event()
        window.destroy.side_effect = closed.set
        guard = app.CloseGuard(parts, window, wait_seconds=3, keep_running=lambda: True)
        shown = []
        guard.show = lambda: shown.append(1)
        guard.quit()
        self.assertTrue(closed.wait(5))
        self.assertEqual(shown, [1])
        self.assertIn("hawkeye-closing", window.evaluate_js.call_args[0][0])
        parts.manager.cancel.assert_called_once()
        guard.quit()                                 # a second Quit while stopping does nothing more
        self.assertEqual(window.destroy.call_count, 1)

    def test_hidden_hint_names_the_right_place(self):
        self.assertIn("شريط القوائم", app.hidden_hint("ar", "darwin"))
        self.assertIn("الساعة", app.hidden_hint("ar", "win32"))
        self.assertIn("menu bar", app.hidden_hint("en", "darwin"))
        self.assertIn("clock", app.hidden_hint("en", "linux"))

    def test_close_guard_closes_at_once_when_idle(self):
        parts = MagicMock()
        parts.manager.is_running = False
        window = MagicMock()
        self.assertTrue(app.CloseGuard(parts, window).on_closing())
        window.evaluate_js.assert_not_called()

    def test_close_guard_keeps_window_until_search_stops(self):
        import threading
        parts = MagicMock()
        parts.manager.is_running = True
        window = MagicMock()
        destroyed = threading.Event()
        window.destroy.side_effect = destroyed.set
        guard = app.CloseGuard(parts, window, wait_seconds=3)
        self.assertFalse(guard.on_closing())
        self.assertTrue(destroyed.wait(5))
        self.assertIn("hawkeye-closing", window.evaluate_js.call_args[0][0])
        names = [c[0] for c in parts.mock_calls if c[0] in (
            "auto.stop", "manager.close", "manager.cancel", "manager.wait")]
        self.assertEqual(names, ["auto.stop", "manager.close", "manager.cancel", "manager.wait"])
        parts.manager.wait.assert_called_once_with(3)
        # A second close request while stopping closes at once.
        self.assertTrue(guard.on_closing())

    def test_main_survives_report_failure_and_still_shuts_down(self):
        from unittest.mock import patch
        parts = MagicMock()
        parts.manager.report_pending_now.side_effect = RuntimeError("db locked")
        thread = MagicMock()
        thread.is_alive.return_value = False
        with patch.object(app, "setup_logging"), patch.object(app, "_prepare_data"), \
                patch.object(app, "SingleInstance"), \
                patch.object(app, "build", return_value=parts), patch.object(app, "_serve", return_value=thread), \
                patch.object(app, "shutdown") as shut, self.assertLogs("app", "ERROR"), \
                patch("builtins.print"), patch("sys.stderr"):
            with self.assertRaises(SystemExit) as ctx:
                app.main(["--no-window"])
        self.assertEqual(ctx.exception.code, 1)  # server thread was dead
        parts.manager.report_pending_now.assert_called_once()
        shut.assert_called_once_with(parts)

    def test_port_option_reaches_build(self):
        self.assertEqual(app._parse(["--no-window", "--port", "8765"]).port, 8765)
        self.assertIsNone(app._parse([]).port)

    def test_main_recovers_then_starts_auto_then_reports(self):
        from unittest.mock import patch
        parent = MagicMock()
        parts = MagicMock()
        parts.manager = parent.manager
        parts.auto = parent.auto
        thread = MagicMock()
        thread.is_alive.return_value = False
        with patch.object(app, "setup_logging"), patch.object(app, "_prepare_data"), \
                patch.object(app, "SingleInstance"), \
                patch.object(app, "build", return_value=parts) as build, \
                patch.object(app, "_serve", return_value=thread), patch.object(app, "shutdown"), \
                self.assertLogs("app", "ERROR"), patch("builtins.print"), patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                app.main(["--no-window", "--port", "8765"])
        build.assert_called_once_with(port=8765)
        names = [c[0] for c in parent.mock_calls]
        self.assertEqual(names[:3], ["manager.recover_interrupted", "auto.start", "manager.report_pending_now"])

    def test_prepare_data_never_recovers_runs_without_the_lock(self):
        from unittest.mock import patch
        with patch.object(app, "migrate_legacy_config"), patch.object(app, "Store") as store_cls:
            app._prepare_data()
        store = store_cls.return_value.__enter__.return_value
        store.recover_interrupted_runs.assert_not_called()
        store.import_legacy_seen_file.assert_called_once()


if __name__ == "__main__":
    unittest.main()
