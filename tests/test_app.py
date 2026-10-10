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

    def test_shutdown_cancels_waits_and_stops_everything(self):
        parts = MagicMock()
        app.shutdown(parts, wait_seconds=1)
        parts.manager.cancel.assert_called_once()
        parts.manager.wait.assert_called_once_with(1)
        parts.auto.stop.assert_called_once()
        parts.manager.close_subscribers.assert_called_once()
        self.assertTrue(parts.server.should_exit)


if __name__ == "__main__":
    unittest.main()
