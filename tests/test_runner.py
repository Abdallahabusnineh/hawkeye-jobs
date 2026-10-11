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
from runner import AutoSearch, NoSearchSettings, RunInProgress, RunManager
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
        self.calls.append({"settings": settings, "trigger": trigger, "job_age_days": kwargs.get("job_age_days")})
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
        # A cleanup, not tearDown: cleanups run last-in-first-out, so locks a test registers later
        # are released before the folder is deleted (Windows cannot delete an open file).
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "t.db"
        self.lock_path = Path(self.tmp.name) / "run.lock"
        self.app = {"last_posts_run": None, "language": "en"}
        self.saved_app = []
        self.notified = []
        self.search = FakeSearch()
        self.settings = dict(SETTINGS)

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

    def test_search_uses_the_saved_job_age(self):
        self.app["job_age_days"] = 30
        manager = self.make()
        manager.start()
        manager.wait(5)
        self.assertEqual(self.search.calls[0]["job_age_days"], 30)

    def test_start_after_close_is_refused(self):
        manager = self.make()
        manager.close()
        with self.assertRaises(RunInProgress):
            manager.start()
        self.assertEqual(self.search.calls, [])

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

    def test_exclusive_refused_while_other_lock_holder_or_run_alive(self):
        other = RunLock(self.lock_path)
        self.assertTrue(other.acquire())
        try:
            with self.assertRaises(RunInProgress):
                with self.make().exclusive():
                    self.fail("must not enter")
        finally:
            other.release()
        hold = threading.Event()
        self.search = FakeSearch(hold=hold)
        manager = self.make()
        manager.start()
        with self.assertRaises(RunInProgress):
            with manager.exclusive():
                self.fail("must not enter")
        hold.set()
        manager.wait(5)

    def test_start_refused_inside_exclusive_and_lock_released_after(self):
        manager = self.make()
        with manager.exclusive():
            with self.assertRaises(RunInProgress):
                manager.start()
            other = RunLock(self.lock_path)
            self.assertFalse(other.acquire())  # terminal mode is held off too
        self.assertEqual(self.search.calls, [])
        other = RunLock(self.lock_path)
        self.assertTrue(other.acquire())
        other.release()
        manager.start()  # works again
        manager.wait(5)

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


class RobustnessTests(RunnerTestCase):
    def test_lock_released_when_start_fails_after_acquire(self):
        def bad_loader():
            raise RuntimeError("settings unreadable")
        manager = self.make(app_settings_loader=bad_loader)
        with self.assertRaises(RuntimeError):
            manager.start()
        self.assertFalse(manager.is_running)
        other = RunLock(self.lock_path)
        self.assertTrue(other.acquire())
        other.release()

    def test_lock_released_and_current_cleared_when_store_close_fails(self):
        class BadClose(Store):
            def close(self):
                super().close()
                raise RuntimeError("close failed")
        manager = self.make(store_factory=lambda: BadClose(self.db))
        manager.start()
        manager.wait(5)
        self.assertIsNone(manager.current)
        other = RunLock(self.lock_path)
        self.assertTrue(other.acquire())
        other.release()

    def test_failed_posts_mark_still_blocks_second_run_within_24h(self):
        def bad_saver(patch):
            raise OSError("disk full")
        manager = self.make(app_settings_saver=bad_saver, clock=lambda: 1_000_000.0)
        with self.assertLogs("runner", "ERROR"):
            manager.start()
            manager.wait(5)
        q = manager.subscribe()
        manager.start()
        manager.wait(5)
        self.assertNotIn("linkedin_posts", self.search.calls[1]["settings"]["sources"])
        self.assertIn({"type": "posts_skipped", "reason": "daily_limit"}, self.drain(q))


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

    def test_report_pending_now_skipped_while_another_process_holds_the_lock(self):
        with Store(self.db) as store:
            run_id = store.start_run(SETTINGS)
            store.add_jobs(run_id, [job(7)])  # a terminal run saved this and has not reported yet
        other = RunLock(self.lock_path)
        self.assertTrue(other.acquire())
        self.addCleanup(other.release)
        manager = self.make()
        self.assertEqual(manager.report_pending_now(), 0)
        self.assertEqual(self.notified, [])
        with Store(self.db) as store:
            self.assertEqual(len(store.unnotified_jobs()), 1)

    def test_report_pending_now_releases_the_lock_and_respects_close(self):
        with Store(self.db) as store:
            run_id = store.start_run(SETTINGS)
            store.add_jobs(run_id, [job(7)])
        manager = self.make()
        self.assertEqual(manager.report_pending_now(), 1)
        probe = RunLock(self.lock_path)
        self.assertTrue(probe.acquire())
        probe.release()
        with Store(self.db) as store:
            run_id = store.start_run(SETTINGS)
            store.add_jobs(run_id, [job(8)])
        manager.close()
        self.assertEqual(manager.report_pending_now(), 0)

    def test_recover_interrupted_marks_stale_run_failed(self):
        with Store(self.db) as store:
            run_id = store.start_run(SETTINGS)
        self.assertEqual(self.make().recover_interrupted(), 1)
        with Store(self.db) as store:
            self.assertEqual(store.conn.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()[0],
                             "failed")

    def test_recover_interrupted_skipped_while_another_process_holds_the_lock(self):
        with Store(self.db) as store:
            run_id = store.start_run(SETTINGS)
        other = RunLock(self.lock_path)
        self.assertTrue(other.acquire())
        self.addCleanup(other.release)
        self.assertEqual(self.make().recover_interrupted(), 0)
        with Store(self.db) as store:
            self.assertEqual(store.conn.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()[0],
                             "running")


class AutoSearchTests(RunnerTestCase):
    def finished_run_at(self, iso):
        with Store(self.db) as store:
            run_id = store.start_run(SETTINGS)
            store.finish_run(run_id, "done", 0, 0)
            store.conn.execute("UPDATE runs SET finished_at = ? WHERE id = ?", (iso, run_id))
            store.conn.commit()

    def auto(self, manager, now, enabled=True, hours=6, onboarded=True):
        app = {"auto_search": {"enabled": enabled, "hours": hours}, "onboarding_done": onboarded}
        return AutoSearch(manager, app_settings_loader=lambda: app, store_factory=lambda: Store(self.db),
                          clock=lambda: now, interval=0.05)

    def test_not_due_without_any_finished_run(self):
        self.assertFalse(self.auto(self.make(), now=10_000_000).due())

    def test_due_after_interval(self):
        self.finished_run_at("1970-04-26T17:46:40Z")  # 10_000_000
        manager = self.make()
        self.assertFalse(self.auto(manager, now=10_000_000 + 6 * 3600 - 1).due())
        self.assertTrue(self.auto(manager, now=10_000_000 + 6 * 3600).due())

    def test_disabled_or_not_onboarded_is_never_due(self):
        self.finished_run_at("1970-04-26T17:46:40Z")
        manager = self.make()
        self.assertFalse(self.auto(manager, now=20_000_000, enabled=False).due())
        self.assertFalse(self.auto(manager, now=20_000_000, onboarded=False).due())

    def test_tick_starts_auto_run(self):
        self.finished_run_at("1970-04-26T17:46:40Z")
        manager = self.make()
        self.assertTrue(self.auto(manager, now=20_000_000).tick())
        manager.wait(5)
        self.assertEqual(self.search.calls[0]["trigger"], "auto")

    def test_tick_skips_while_running(self):
        self.finished_run_at("1970-04-26T17:46:40Z")
        hold = threading.Event()
        self.search = FakeSearch(hold=hold)
        manager = self.make()
        manager.start()
        self.assertFalse(self.auto(manager, now=20_000_000).tick())
        hold.set()
        manager.wait(5)
        self.assertEqual(len(self.search.calls), 1)

    def test_tick_skips_when_terminal_holds_lock(self):
        self.finished_run_at("1970-04-26T17:46:40Z")
        other = RunLock(self.lock_path)
        self.assertTrue(other.acquire())
        try:
            self.assertFalse(self.auto(self.make(), now=20_000_000).tick())
        finally:
            other.release()

    def test_tick_survives_unexpected_errors(self):
        def boom():
            raise ValueError("broken settings")
        auto = AutoSearch(self.make(), app_settings_loader=boom, store_factory=lambda: Store(self.db),
                          clock=lambda: 20_000_000, interval=0.05)
        with self.assertLogs("runner", level="ERROR"):
            self.assertFalse(auto.tick())

    def test_tick_skips_without_search_settings(self):
        self.finished_run_at("1970-04-26T17:46:40Z")
        self.settings = None
        manager = self.make()
        self.assertFalse(self.auto(manager, now=20_000_000).tick())

    def test_background_thread_ticks_and_stops(self):
        self.finished_run_at("1970-04-26T17:46:40Z")
        manager = self.make()
        auto = self.auto(manager, now=20_000_000)
        auto.start()
        deadline = time.monotonic() + 5
        while not self.search.calls:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        thread = auto._thread
        auto.stop()
        self.assertFalse(thread.is_alive())
        manager.wait(5)

    def test_failing_run_is_not_retried_every_tick(self):
        self.finished_run_at("1970-04-26T17:46:40Z")
        calls = []

        def crash(settings, **kwargs):
            calls.append(1)
            raise RuntimeError("crashed before run row")
        manager = self.make(run_search=crash)
        now = [20_000_000]
        app = {"auto_search": {"enabled": True, "hours": 6}, "onboarding_done": True}
        auto = AutoSearch(manager, app_settings_loader=lambda: app, store_factory=lambda: Store(self.db),
                          clock=lambda: now[0], interval=0.05)
        for _ in range(5):
            auto.tick()
            manager.wait(5)
            now[0] += 60
        self.assertEqual(len(calls), 1)
        now[0] += 6 * 3600
        auto.tick()
        manager.wait(5)
        self.assertEqual(len(calls), 2)

    def test_invalid_hours_fall_back_to_six(self):
        self.finished_run_at("1970-04-26T17:46:40Z")
        manager = self.make()
        for bad in (None, 0, "x", 7):
            app = {"auto_search": {"enabled": True, "hours": bad}, "onboarding_done": True}
            def make(now):
                return AutoSearch(manager, app_settings_loader=lambda: app,
                                  store_factory=lambda: Store(self.db), clock=lambda: now)
            self.assertFalse(make(10_000_000 + 6 * 3600 - 1).due())
            self.assertTrue(make(10_000_000 + 6 * 3600).due())


if __name__ == "__main__":
    unittest.main()
