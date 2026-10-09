import logging
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from browser import ChromeNotFound
from linkedin_posts import LinkedInLoginRequired
import pipeline
from pipeline import run_search
from store import Store


def job_for(source, args, url=None):
    return {
        "title": f"Java Developer ({source})",
        "url": url or f"https://example.com/{source}/{quote(str(args), safe='')}",
        "description": "Acme - Amman",
        "source": source,
        "location": "x",
        "published": "",
    }


class FakeDriver:
    def __init__(self):
        self.alive = True
        self.quit_called = False

    @property
    def window_handles(self):
        if not self.alive:
            raise RuntimeError("invalid session id")
        return ["main"]

    def quit(self):
        self.quit_called = True


class Recorder:
    """Fake fetchers + driver factory that record what ran."""

    def __init__(self):
        self.calls = []
        self.drivers = []
        self.lock = threading.Lock()
        self.behaviour = {}  # source -> callable(driver_or_None, args) that may raise / return jobs

    def http(self, source):
        def fetch(args, seen_urls, keywords):
            with self.lock:
                self.calls.append((source, args))
            if source in self.behaviour:
                return self.behaviour[source](None, args)
            return [job_for(source, args)]
        return fetch

    def browser(self, source):
        def fetch(driver, args, seen_urls, keywords):
            with self.lock:
                self.calls.append((source, args))
            if source in self.behaviour:
                return self.behaviour[source](driver, args)
            return [job_for(source, args)]
        return fetch

    def fetchers(self):
        result = {s: self.http(s) for s in ("linkedin", "indeed")}
        result.update({s: self.browser(s) for s in ("bayt", "naukrigulf", "google_jobs", "linkedin_posts")})
        return result

    def driver_factory(self):
        driver = FakeDriver()
        self.drivers.append(driver)
        return driver


def settings(keywords=("Java",), locations=("jo",), sources=("linkedin", "bayt")):
    return {"keywords": list(keywords), "locations": list(locations), "sources": list(sources)}


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "test.db")
        self.rec = Recorder()
        self.events = []
        # Expected failures log tracebacks; keep the test output clean.
        self.null_handler = logging.NullHandler()
        logging.getLogger("pipeline").addHandler(self.null_handler)

    def tearDown(self):
        logging.getLogger("pipeline").removeHandler(self.null_handler)
        self.store.close()
        self.tmp.cleanup()

    def run_search(self, cfg, **kwargs):
        kwargs.setdefault("store", self.store)
        kwargs.setdefault("fetchers", self.rec.fetchers())
        kwargs.setdefault("driver_factory", self.rec.driver_factory)
        kwargs.setdefault("on_event", self.events.append)
        kwargs.setdefault("delays", {})  # no throttling unless a test asks for it
        return run_search(cfg, **kwargs)

    def types(self):
        return [e["type"] for e in self.events]

    def of_type(self, kind):
        return [e for e in self.events if e["type"] == kind]


class HappyPathTests(PipelineTestCase):
    def test_events_order_and_totals(self):
        summary = self.run_search(settings())
        self.assertEqual(self.events[0], {"type": "run_started", "run_id": summary.run_id, "total": 2})
        self.assertEqual(self.events[-1], {"type": "run_finished", "run_id": summary.run_id,
                                           "state": "done", "found": 2, "new": 2})
        for done in self.of_type("query_done"):
            started_at = self.events.index({k: done[k] for k in ("index", "source", "keyword", "location_id")}
                                           | {"type": "query_started"})
            self.assertLess(started_at, self.events.index(done))
        self.assertEqual((summary.state, summary.found, len(summary.new_jobs)), ("done", 2, 2))
        rows = self.store.list_jobs()
        self.assertEqual({(r["keyword"], r["location_id"]) for r in rows}, {("Java", "jo")})
        self.assertTrue(self.rec.drivers[0].quit_called)

    def test_second_run_reports_no_new_jobs(self):
        self.run_search(settings())
        self.events.clear()
        summary = self.run_search(settings())
        self.assertEqual((summary.found, summary.new_jobs), (2, []))
        self.assertEqual(self.events[-1]["new"], 0)

    def test_browser_lane_runs_sources_in_order(self):
        self.run_search(settings(sources=("linkedin_posts", "google_jobs", "bayt")))
        order = [source for source, _ in self.rec.calls]
        self.assertEqual(order, ["bayt", "google_jobs", "linkedin_posts", "linkedin_posts"])

    def test_no_driver_without_browser_sources(self):
        summary = self.run_search(settings(sources=("linkedin",)))
        self.assertEqual(summary.state, "done")
        self.assertEqual(self.rec.drivers, [])

    def test_no_queries_finishes_immediately(self):
        summary = self.run_search(settings(sources=("naukrigulf",)))  # NaukriGulf has no Jordan
        self.assertEqual(self.types(), ["run_started", "run_finished"])
        self.assertEqual(self.events[0]["total"], 0)
        self.assertEqual(summary.state, "done")

    def test_jobs_saved_per_query(self):
        counts = []

        def on_event(event):
            if event["type"] == "query_done":
                counts.append(len(self.store.list_jobs()))

        self.run_search(settings(keywords=("Java", "Kotlin"), sources=("linkedin",)), on_event=on_event)
        self.assertEqual(counts, [1, 2])

    def test_same_url_from_both_lanes_is_new_once(self):
        shared = "https://jobs.example.com/same"
        self.rec.behaviour["linkedin"] = lambda d, a: [job_for("linkedin", a, url=shared)]
        self.rec.behaviour["bayt"] = lambda d, a: [job_for("bayt", a, url=shared)]
        summary = self.run_search(settings())
        self.assertEqual(summary.found, 2)
        self.assertEqual(len(summary.new_jobs), 1)

    def test_on_event_exception_is_ignored(self):
        def broken(event):
            raise ValueError("UI bug")
        summary = self.run_search(settings(), on_event=broken)
        self.assertEqual(summary.state, "done")


class FailureTests(PipelineTestCase):
    def test_failing_query_does_not_stop_run(self):
        def boom(driver, args):
            raise ValueError("bad page")
        self.rec.behaviour["linkedin"] = boom
        summary = self.run_search(settings())
        failed = self.of_type("query_failed")
        self.assertEqual([(e["source"], e["error"]) for e in failed], [("linkedin", "ValueError")])
        self.assertEqual([e["source"] for e in self.of_type("query_done")], ["bayt"])
        self.assertEqual(summary.state, "done")

    def test_login_required_skips_remaining_posts(self):
        def logged_out(driver, args):
            raise LinkedInLoginRequired("https://www.linkedin.com/authwall")
        self.rec.behaviour["linkedin_posts"] = logged_out
        summary = self.run_search(settings(sources=("linkedin_posts",)))
        self.assertEqual(len(self.rec.calls), 1)
        self.assertEqual(self.of_type("login_required"), [{"type": "login_required", "source": "linkedin_posts"}])
        self.assertEqual(self.of_type("query_failed")[0]["error"], "LinkedInLoginRequired")
        skipped = self.of_type("query_skipped")
        self.assertEqual([(e["index"], e["reason"]) for e in skipped], [(1, "login_required")])
        self.assertEqual(summary.state, "done")

    def test_chrome_not_found_skips_browser_but_http_completes(self):
        def no_chrome():
            raise ChromeNotFound("Google Chrome is not installed")
        summary = self.run_search(settings(), driver_factory=no_chrome)
        self.assertEqual(self.of_type("browser_failed"), [{"type": "browser_failed", "reason": "chrome_not_found"}])
        self.assertEqual([(e["source"], e["reason"]) for e in self.of_type("query_skipped")],
                         [("bayt", "browser_unavailable")])
        self.assertEqual([e["source"] for e in self.of_type("query_done")], ["linkedin"])
        self.assertEqual(summary.state, "done")

    def test_start_failure_reason(self):
        def broken():
            raise RuntimeError("session not created")
        self.run_search(settings(sources=("bayt",)), driver_factory=broken)
        self.assertEqual(self.of_type("browser_failed")[0]["reason"], "start_failed")

    def test_single_crash_restarts_driver(self):
        crashed = []

        def crash_once(driver, args):
            if not crashed:
                crashed.append(args)
                driver.alive = False
                raise RuntimeError("invalid session id")
            return [job_for("bayt", args)]

        self.rec.behaviour["bayt"] = crash_once
        summary = self.run_search(settings(keywords=("Java", "Kotlin"), sources=("bayt",)))
        self.assertEqual(len(self.rec.drivers), 2)
        self.assertTrue(all(d.quit_called for d in self.rec.drivers))
        self.assertEqual([e["index"] for e in self.of_type("query_failed")], [0])
        self.assertEqual([e["index"] for e in self.of_type("query_done")], [1])
        self.assertEqual(self.of_type("browser_failed"), [])
        self.assertEqual(summary.state, "done")

    def test_second_crash_skips_rest(self):
        def always_crash(driver, args):
            driver.alive = False
            raise RuntimeError("invalid session id")

        self.rec.behaviour["bayt"] = always_crash
        self.run_search(settings(keywords=("Java", "Kotlin", "Go"), sources=("bayt",)))
        self.assertEqual([e["index"] for e in self.of_type("query_failed")], [0, 1])
        self.assertEqual(self.of_type("browser_failed"), [{"type": "browser_failed", "reason": "crashed"}])
        self.assertEqual([(e["index"], e["reason"]) for e in self.of_type("query_skipped")],
                         [(2, "browser_unavailable")])

    def test_driver_quit_after_failing_query(self):
        def boom(driver, args):
            raise ValueError("bad page")
        self.rec.behaviour["bayt"] = boom
        self.run_search(settings(sources=("bayt",)))
        self.assertEqual(len(self.rec.drivers), 1)
        self.assertTrue(self.rec.drivers[0].quit_called)

    def test_store_failure_marks_run_failed(self):
        class BrokenStore(Store):
            def add_jobs(self, run_id, jobs):
                raise sqlite3.OperationalError("database is locked")

        broken = BrokenStore(Path(self.tmp.name) / "broken.db")
        try:
            summary = self.run_search(settings(), store=broken)
            self.assertEqual(summary.state, "failed")
            self.assertEqual(self.events[-1]["state"], "failed")
            self.assertEqual(broken.list_runs()[0]["state"], "failed")
        finally:
            broken.close()


class CancelTests(PipelineTestCase):
    def test_ctrl_c_during_failure_join_still_finishes_run(self):
        class BrokenStore(Store):
            def add_jobs(self, run_id, jobs):
                second_started.wait(5)  # fail only once the lane is inside its 2nd query
                raise sqlite3.OperationalError("database is locked")

        broken = BrokenStore(Path(self.tmp.name) / "broken2.db")

        second_started = threading.Event()
        calls = []

        def slow(driver, args):  # keep the lane busy while the caller is in join()
            calls.append(args)
            if len(calls) == 2:
                second_started.set()
            time.sleep(0.5)
            return [job_for("bayt", args)]

        self.rec.behaviour["bayt"] = slow
        real_join = threading.Thread.join
        interrupted = []

        def join_once_interrupted(thread, *args, **kwargs):
            if thread.name.startswith("hawkeye-") and not interrupted:
                interrupted.append(True)
                raise KeyboardInterrupt
            return real_join(thread, *args, **kwargs)

        try:
            with mock.patch.object(threading.Thread, "join", join_once_interrupted):
                summary = self.run_search(settings(keywords=("Java", "Kotlin"), sources=("bayt",)), store=broken)
            self.assertEqual(interrupted, [True])
            self.assertEqual(summary.state, "failed")
            self.assertEqual(self.events[-1]["type"], "run_finished")
            self.assertEqual(broken.list_runs()[0]["state"], "failed")
            self.assertTrue(all(d.quit_called for d in self.rec.drivers))
            self.assertEqual(len(self.rec.drivers), 1)
        finally:
            broken.close()

    def test_cancel_with_crashed_driver_does_not_restart_chrome(self):
        cancel = threading.Event()

        def die_with_cancel(driver, args):
            cancel.set()
            driver.alive = False
            raise RuntimeError("chrome died with the process group")

        self.rec.behaviour["bayt"] = die_with_cancel
        summary = self.run_search(settings(keywords=("Java", "Kotlin"), sources=("bayt",)), cancel=cancel)
        self.assertEqual(len(self.rec.drivers), 1)
        self.assertEqual(self.of_type("browser_failed"), [])
        self.assertEqual(self.of_type("query_failed"), [])
        self.assertEqual(summary.state, "cancelled")
        self.assertTrue(self.rec.drivers[0].quit_called)

    def test_cancel_with_login_required_emits_no_login_event(self):
        cancel = threading.Event()

        def login_with_cancel(driver, args):
            cancel.set()
            raise LinkedInLoginRequired("https://www.linkedin.com/authwall")

        self.rec.behaviour["linkedin_posts"] = login_with_cancel
        summary = self.run_search(settings(keywords=("Java", "Kotlin"), sources=("linkedin_posts",)), cancel=cancel)
        self.assertEqual(self.of_type("login_required"), [])
        self.assertEqual(self.of_type("query_failed"), [])
        self.assertEqual(summary.state, "cancelled")

    def test_cancel_stops_after_in_flight_query(self):
        cancel = threading.Event()

        def cancel_during_first(driver, args):
            cancel.set()
            return [job_for("linkedin", args)]

        self.rec.behaviour["linkedin"] = cancel_during_first
        summary = self.run_search(settings(keywords=("Java", "Kotlin", "Go"), sources=("linkedin",)), cancel=cancel)
        self.assertEqual(len(self.rec.calls), 1)
        self.assertEqual(summary.state, "cancelled")
        self.assertEqual(len(summary.new_jobs), 1)
        self.assertEqual(self.events[-1]["state"], "cancelled")
        self.assertEqual(self.store.list_runs()[0]["state"], "cancelled")

    def test_ctrl_c_in_caller_cancels_run(self):
        def interrupt(event):
            if event["type"] == "query_started":
                raise KeyboardInterrupt
        summary = self.run_search(settings(keywords=("Java", "Kotlin", "Go"), sources=("linkedin",)),
                                  on_event=interrupt)
        self.assertEqual(summary.state, "cancelled")


class ThrottleTests(PipelineTestCase):
    def test_consecutive_requests_to_one_site_are_spaced(self):
        starts = []

        def stamp(driver, args):
            starts.append(time.monotonic())
            return [job_for("bayt", args)]

        self.rec.behaviour["bayt"] = stamp
        summary = self.run_search(settings(keywords=("Java", "Kotlin", "Go"), sources=("bayt",)),
                                  delays={"bayt": (0.05, 0.05)})
        self.assertEqual(summary.state, "done")
        self.assertEqual(len(starts), 3)
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        self.assertTrue(all(gap >= 0.05 - 0.01 for gap in gaps), gaps)

    def test_cancel_while_waiting_on_throttle_ends_run_quickly(self):
        cancel = threading.Event()

        def cancel_on_first(driver, args):
            cancel.set()
            return [job_for("linkedin", args)]

        self.rec.behaviour["linkedin"] = cancel_on_first
        started = time.monotonic()
        summary = self.run_search(settings(keywords=("Java", "Kotlin"), sources=("linkedin",)),
                                  cancel=cancel, delays={"linkedin": (30, 30)})
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(len(self.rec.calls), 1)
        self.assertEqual(summary.state, "cancelled")
        self.assertEqual(len(self.of_type("query_started")), 1)

    def test_cancel_during_throttle_wait_in_browser_lane(self):
        cancel = threading.Event()
        timer = threading.Timer(0.3, cancel.set)
        self.addCleanup(timer.cancel)
        timer.start()
        started = time.monotonic()
        summary = self.run_search(settings(keywords=("Java", "Kotlin"), sources=("bayt",)),
                                  cancel=cancel, delays={"bayt": (30, 30)})
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(len(self.rec.calls), 1)
        self.assertEqual(summary.state, "cancelled")
        self.assertTrue(self.rec.drivers[0].quit_called)

    def test_default_delays_are_the_polite_table(self):
        built = []

        class RecordingThrottle:
            def __init__(self, low, high, *args, **kwargs):
                self.gap = (low, high)
                self.waits = 0
                built.append(self)

            def wait(self, cancel=None):
                self.waits += 1
                return True

            def back_off(self):
                pass

        fetched_while_waited = []

        def check(driver, args):
            bayt = [t for t in built if t.gap == (6, 10)]
            fetched_while_waited.append(bool(bayt) and bayt[0].waits == 1)
            return [job_for("bayt", args)]

        self.rec.behaviour["bayt"] = check
        with mock.patch.object(pipeline, "Throttle", RecordingThrottle):
            kwargs = {"store": self.store, "fetchers": self.rec.fetchers(),
                      "driver_factory": self.rec.driver_factory}
            run_search(settings(sources=("bayt",)), **kwargs)
        self.assertEqual(fetched_while_waited, [True])
        self.assertEqual(sorted(t.gap for t in built),
                         sorted([(10, 20), (3, 6), (6, 10), (4, 7), (8, 12)]))

    def test_linkedin_sources_share_one_throttle_at_the_largest_gap(self):
        starts = []

        def stamp(driver, args):
            starts.append(time.monotonic())
            return [job_for("linkedin_posts", args)]

        def stamp_http(driver, args):
            starts.append(time.monotonic())
            return [job_for("linkedin", args)]

        self.rec.behaviour["linkedin_posts"] = stamp
        self.rec.behaviour["linkedin"] = stamp_http
        self.run_search(settings(keywords=("Java", "Kotlin"), sources=("linkedin", "linkedin_posts")),
                        delays={"linkedin": (0.05, 0.05), "linkedin_posts": (0.1, 0.1)})
        starts.sort()
        self.assertGreaterEqual(len(starts), 4)
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        self.assertTrue(all(g >= 0.1 - 0.01 for g in gaps), gaps)

    def test_group_gap_is_per_field_maximum(self):
        built = []

        class RecordingThrottle:
            def __init__(self, low, high, *args, **kwargs):
                built.append((low, high))

        with mock.patch.object(pipeline, "Throttle", RecordingThrottle):
            throttles = pipeline._make_throttles({"linkedin": (6, 7), "linkedin_posts": (5, 30)})
        self.assertEqual(built, [(6, 30)])
        self.assertIs(throttles["linkedin"], throttles["linkedin_posts"])

    def test_gap_doubles_after_a_failed_query(self):
        starts = []

        def fail_first(driver, args):
            starts.append(time.monotonic())
            if len(starts) == 1:
                raise ValueError("blocked?")
            return [job_for("bayt", args)]

        self.rec.behaviour["bayt"] = fail_first
        self.run_search(settings(keywords=("Java", "Kotlin", "Go"), sources=("bayt",)),
                        delays={"bayt": (0.05, 0.05)})
        self.assertEqual(len(starts), 3)
        self.assertGreaterEqual(starts[1] - starts[0], 0.1 - 0.01)

    def test_no_back_off_when_failure_is_a_cancel(self):
        cancel = threading.Event()

        def die_with_cancel(driver, args):
            cancel.set()
            raise RuntimeError("killed")

        self.rec.behaviour["linkedin"] = die_with_cancel
        throttles = []
        real = pipeline.Throttle

        def spy(*a, **k):
            throttles.append(real(*a, **k))
            throttles[-1].back_off = mock.Mock()
            return throttles[-1]

        with mock.patch.object(pipeline, "Throttle", spy):
            self.run_search(settings(keywords=("Java", "Kotlin"), sources=("linkedin",)),
                            cancel=cancel, delays={"linkedin": (0.01, 0.01)})
        throttles[0].back_off.assert_not_called()

    def test_source_missing_from_delays_is_not_throttled(self):
        started = time.monotonic()
        self.run_search(settings(keywords=("Java", "Kotlin"), sources=("linkedin",)),
                        delays={"bayt": (30, 30)})
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(len(self.rec.calls), 2)


if __name__ == "__main__":
    unittest.main()
