import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

from reporting import REPORT_LIMIT, report_pending
from store import Store


def job(n):
    return {"title": f"Java Developer {n}", "url": f"https://a.com/{n}", "description": "Acme",
            "source": "Bayt", "location": "jo", "published": ""}


class ReportPendingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        run_id = self.store.start_run({"keywords": ["Java"], "locations": ["jo"], "sources": ["bayt"]})
        self.store.add_jobs(run_id, [job(i) for i in range(3)])
        self.calls = []

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def notifier(self, result):
        def notify(jobs):
            self.calls.append([j["url"] for j in jobs])
            return result
        return notify

    def test_default_limit(self):
        self.assertEqual(REPORT_LIMIT, 500)

    def test_reports_all_pending_and_marks_them(self):
        result = report_pending(self.store, self.notifier(True))
        self.assertEqual((result.reported, result.more_waiting, result.delivered), (3, False, True))
        self.assertEqual(len(self.calls[0]), 3)
        self.assertEqual(self.store.unnotified_jobs(), [])

    def test_failed_delivery_leaves_jobs_pending(self):
        result = report_pending(self.store, self.notifier(False))
        self.assertEqual((result.reported, result.delivered), (0, False))
        self.assertEqual(len(self.store.unnotified_jobs()), 3)

    def test_limit_and_more_waiting(self):
        result = report_pending(self.store, self.notifier(True), limit=2)
        self.assertEqual((result.reported, result.more_waiting), (2, True))
        self.assertEqual(len(self.store.unnotified_jobs()), 1)

    def test_nothing_pending_does_not_call_notifier(self):
        report_pending(self.store, self.notifier(True))
        self.calls.clear()
        result = report_pending(self.store, self.notifier(True))
        self.assertEqual((result.reported, result.jobs), (0, []))
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
