import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

import notify


def jobs(n):
    return [{"title": f"Java Developer {i}"} for i in range(n)]


class FakeBackend:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send(self, title, message):
        if self.fail:
            raise RuntimeError("notifications not authorised")
        self.sent.append((title, message))


class FormatTests(unittest.TestCase):
    def test_arabic_message(self):
        title, body = notify.format_message(jobs(5), "ar")
        self.assertEqual(title, "Hawkeye Jobs")
        self.assertTrue(body.startswith("وظائف جديدة: 5"))
        self.assertIn("• Java Developer 0", body)
        self.assertNotIn("Java Developer 3", body)
        self.assertTrue(body.endswith("+2"))

    def test_english_and_unknown_language(self):
        self.assertTrue(notify.format_message(jobs(1), "en")[1].startswith("New jobs: 1"))
        self.assertTrue(notify.format_message(jobs(1), "fr")[1].startswith("New jobs: 1"))


class DesktopNotifierTests(unittest.TestCase):
    def test_sends_in_current_language(self):
        backend = FakeBackend()
        language = ["ar"]
        notifier = notify.DesktopNotifier(lambda: language[0], backend=backend)
        self.assertTrue(notifier(jobs(2)))
        language[0] = "en"
        notifier(jobs(1))
        self.assertTrue(backend.sent[0][1].startswith("وظائف جديدة: 2"))
        self.assertTrue(backend.sent[1][1].startswith("New jobs: 1"))

    def test_backend_failure_still_counts_as_reported(self):
        notifier = notify.DesktopNotifier(lambda: "en", backend=FakeBackend(fail=True))
        with self.assertLogs("notify", "WARNING"):
            self.assertTrue(notifier(jobs(1)))

    def test_no_jobs_sends_nothing(self):
        backend = FakeBackend()
        self.assertTrue(notify.DesktopNotifier(lambda: "en", backend=backend)([]))
        self.assertEqual(backend.sent, [])


if __name__ == "__main__":
    unittest.main()
