import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

from run_lock import RunLock
from pipeline import RunSummary
from runner import NoSearchSettings, RunInProgress, RunManager
from store import Store

SETTINGS = {"keywords": ["Java"], "locations": ["jo"], "sources": ["bayt", "linkedin_posts"]}


def job(n):
    return {"title": f"Java Developer {n}", "url": f"https://a.com/{n}", "description": "Acme",
            "source": "Bayt", "location": "jo", "published": ""}


class FakeSearch:
    """Stands in for pipeline.run_search: emits a short run and saves jobs like the real one."""

    def __init__(self, hold=None):
        self.calls = []
        self.hold = hold  # threading.Event: block mid-run until set

    def __call__(self, settings, on_event=None, cancel=None, *, country_labels=None, store=None, trigger="manual",
                 **kwargs):
        self.calls.append({"settings": settings, "trigger": trigger})
        run_id = store.start_run(settings, trigger=trigger)
        on_event({"type": "run_started", "run_id": run_id, "total": 2})
        q = {"index": 0, "source": "bayt", "keyword": "Java", "location_id": "jo"}
        on_event({"type": "query_started", **q})
        if self.hold is not None:
            while not self.hold.is_set() and not cancel.is_set():
                time.sleep(0.01)
        new = store.add_jobs(run_id, [job(1), job(2)])
        on_event({"type": "query_done", **q, "found": 2, "new": len(new)})
        if "linkedin_posts" in settings["sources"]:
            p = {"index": 1, "source": "linkedin_posts", "keyword": "Java", "location_id": None}
            on_event({"type": "query_started", **p})
            on_event({"type": "query_done", **p, "found": 0, "new": 0})
        state = "cancelled" if cancel.is_set() else "done"
        store.finish_run(run_id, state, 2, len(new))
        on_event({"type": "run_finished", "run_id": run_id, "state": state, "found": 2, "new": len(new)})
        return RunSummary(run_id=run_id, state=state, found=2, new_jobs=new)


class RunnerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        self.lock_path = Path(self.tmp.name) / "run.lock"
        self.app = {"last_posts_run": None, "language": "en"}
        self.saved_app = []
        self.notified = []
        self.search = FakeSearch()
        self.settings = dict(SETTINGS)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, **kwargs):
        def saver(patch):
            self.saved_app.append(patch)
            self.app.update(patch)
            return dict(self.app)
        defaults = dict(
            notifier=lambda jobs: self.notified.append(len(jobs)) or True,
            run_search=self.search,
            store_factory=lambda: Store(self.db),
            lock=RunLock(self.lock_path),
            settings_loader=lambda: self.settings,
            app_settings_loader=lambda: dict(self.app),
            app_settings_saver=saver,
            country_labels_loader=lambda: {"jo": "Jordan"},
        )
        defaults.update(kwargs)
        return RunManager(**defaults)

    def drain(self, q):
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        return events


class StartTests(RunnerTestCase):
    def test_run_records_queries_reports_and_publishes(self):
        manager = self.make()
        q = manager.subscribe()
        run_id = manager.start("manual")
        self.assertTrue(manager.wait(5))
        self.assertIsNotNone(run_id)
        types = [e["type"] for e in self.drain(q)]
        self.assertEqual(types[0], "run_started")
        self.assertIn("run_finished", types)
        self.assertEqual(types[-1], "jobs_reported")
        with Store(self.db) as store:
            details = store.run_details(run_id)
            self.assertEqual([x["source"] for x in details["queries"]], ["bayt", "linkedin_posts"])
            self.assertEqual(store.unnotified_jobs(), [])
        self.assertEqual(self.notified, [2])
        self.assertFalse(manager.is_running)
        self.assertIsNone(manager.current)

    def test_events_carry_the_trigger_and_current_progress(self):
        hold = threading.Event()
        self.search = FakeSearch(hold=hold)
        manager = self.make()
        q = manager.subscribe()
        manager.start("auto")
        deadline = time.monotonic() + 5
        while manager.current is None or manager.current["total"] != 2:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        self.assertEqual(manager.current["trigger"], "auto")
        hold.set()
        manager.wait(5)
        self.assertTrue(all(e.get("trigger") == "auto" for e in self.drain(q) if e["type"].startswith(("run_", "query_"))))
        self.assertEqual(self.search.calls[0]["trigger"], "auto")

    def test_second_start_while_running_is_refused(self):
        hold = threading.Event()
        self.search = FakeSearch(hold=hold)
        manager = self.make()
        manager.start()
        with self.assertRaises(RunInProgress):
            manager.start()
        hold.set()
        manager.wait(5)

    def test_start_refused_when_file_lock_held_elsewhere(self):
        other = RunLock(self.lock_path)
        self.assertTrue(other.acquire())
        try:
            with self.assertRaises(RunInProgress):
                self.make().start()
            self.assertEqual(self.search.calls, [])
        finally:
            other.release()

    def test_lock_released_after_run(self):
        manager = self.make()
        manager.start()
        manager.wait(5)
        other = RunLock(self.lock_path)
        self.assertTrue(other.acquire())
        other.release()

    def test_no_settings_is_refused(self):
        self.settings = None
        with self.assertRaises(NoSearchSettings):
            self.make().start()
        other = RunLock(self.lock_path)
        self.assertTrue(other.acquire())  # lock was not kept
        other.release()

    def test_cancel(self):
        hold = threading.Event()
        self.search = FakeSearch(hold=hold)
        manager = self.make()
        self.assertFalse(manager.cancel())
        manager.start()
        self.assertTrue(manager.cancel())
        self.assertTrue(manager.wait(5))
        with Store(self.db) as store:
            self.assertEqual(store.list_runs()[0]["state"], "cancelled")

    def test_crash_in_search_publishes_run_error_and_releases(self):
        def boom(*args, **kwargs):
            raise RuntimeError("disk full")
        manager = self.make(run_search=boom)
        q = manager.subscribe()
        with self.assertLogs("runner", "ERROR"):
            manager.start()
            manager.wait(5)
        self.assertIn("run_error", [e["type"] for e in self.drain(q)])
        self.assertFalse(manager.is_running)


class PostsGateTests(RunnerTestCase):
    def test_posts_run_marks_last_posts_run(self):
        manager = self.make(clock=lambda: 1_000_000.0)
        manager.start()
        manager.wait(5)
        self.assertEqual(self.saved_app, [{"last_posts_run": "1970-01-12T13:46:40Z"}])

    def test_posts_skipped_within_24h(self):
        self.app["last_posts_run"] = "1970-01-12T13:46:40Z"
        manager = self.make(clock=lambda: 1_000_000.0 + 3600)
        q = manager.subscribe()
        manager.start()
        manager.wait(5)
        self.assertNotIn("linkedin_posts", self.search.calls[0]["settings"]["sources"])
        self.assertIn({"type": "posts_skipped", "reason": "daily_limit"}, self.drain(q))

    def test_posts_allowed_after_24h(self):
        self.app["last_posts_run"] = "1970-01-12T13:46:40Z"
        manager = self.make(clock=lambda: 1_000_000.0 + 86400)
        manager.start()
        manager.wait(5)
        self.assertIn("linkedin_posts", self.search.calls[0]["settings"]["sources"])


class HistoryFailureTests(RunnerTestCase):
    def test_record_query_failure_does_not_abort_the_run(self):
        class BrokenHistory(Store):
            def record_query(self, run_id, event):
                raise RuntimeError("history write failed")

        manager = self.make(store_factory=lambda: BrokenHistory(self.db))
        q = manager.subscribe()
        with self.assertLogs("runner", "ERROR"):
            manager.start()
            self.assertTrue(manager.wait(5))
        types = [e["type"] for e in self.drain(q)]
        self.assertIn("run_finished", types)
        self.assertEqual(types[-1], "jobs_reported")
        self.assertNotIn("run_error", types)


class SubscriberTests(RunnerTestCase):
    def test_slow_subscriber_is_dropped_others_keep_receiving(self):
        manager = self.make()
        slow = manager.subscribe(maxsize=1)
        fast = manager.subscribe()
        manager.start()
        manager.wait(5)
        self.assertFalse(manager.is_subscribed(slow))
        self.assertTrue(manager.is_subscribed(fast))
        self.assertGreater(len(self.drain(fast)), 3)

    def test_close_subscribers_sends_sentinel(self):
        manager = self.make()
        q = manager.subscribe()
        manager.close_subscribers()
        self.assertIsNone(q.get_nowait())
        self.assertFalse(manager.is_subscribed(q))


class ReportNowTests(RunnerTestCase):
    def test_report_pending_now_notifies_leftovers(self):
        with Store(self.db) as store:
            run_id = store.start_run(SETTINGS)
            store.add_jobs(run_id, [job(7)])  # saved by a run that was killed before reporting
        manager = self.make()
        q = manager.subscribe()
        self.assertEqual(manager.report_pending_now(), 1)
        self.assertEqual(self.notified, [1])
        self.assertEqual(self.drain(q), [{"type": "jobs_reported", "count": 1, "more_waiting": False}])

    def test_report_pending_now_skipped_while_running(self):
        hold = threading.Event()
        self.search = FakeSearch(hold=hold)
        manager = self.make()
        manager.start()
        self.assertEqual(manager.report_pending_now(), 0)
        hold.set()
        manager.wait(5)


if __name__ == "__main__":
    unittest.main()
