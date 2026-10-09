"""
pipeline.py — One search run, shared by the terminal and the app
==================================================================
run_search() plans every query, runs them on two lanes, saves each query's jobs
to the Store as soon as it finishes, and reports progress as plain-data events
(see docs/superpowers/specs/2026-10-10-core-pipeline-design.md §8).

Lanes (one thread each, queries run one after another inside a lane):
    HTTP lane    : linkedin, indeed          (JobSpy, no browser)
    Browser lane : bayt, naukrigulf, google_jobs, linkedin_posts (one Chrome)

Politeness: before each query a lane waits on that site's Throttle (random
minimum gap between request starts, see throttle.py and POLITE_DELAYS), so users
are not flagged as bots. The wait sleeps in short slices and honours `cancel`.

Lanes never touch the Store and never call on_event: they push messages onto a
queue, and the thread that called run_search() does all saving and reporting.
So on_event always runs on the caller's thread, in order.

Cancellation: set the `cancel` event (the terminal does this on Ctrl+C). Each
lane stops before its next query, so cancel takes at most one in-flight query.
"""

import logging
import queue
import threading
from dataclasses import dataclass, field

from browser import ChromeNotFound
from catalog import BROWSER_SOURCES, SOURCE_ORDER, plan_queries
from linkedin_posts import LinkedInLoginRequired
from store import Store
from throttle import Throttle

log = logging.getLogger(__name__)

HTTP_SOURCES = ("linkedin", "indeed")
_QUEUE_POLL_SECONDS = 0.2

POLITE_DELAYS = {  # (min, max) seconds between requests to the same site
    "linkedin": (3, 6),
    "indeed": (3, 6),
    "bayt": (6, 10),
    "naukrigulf": (4, 7),
    "google_jobs": (8, 12),
    "linkedin_posts": (10, 20),  # the user's own LinkedIn account: slowest
}


@dataclass
class RunSummary:
    run_id: int
    state: str  # 'done' | 'cancelled' | 'failed'
    found: int
    new_jobs: list = field(default_factory=list)


def _default_fetchers() -> dict:
    from bayt import fetch_bayt
    from google_jobs import fetch_google_jobs
    from linkedin_posts import fetch_linkedin_posts
    from naukrigulf import fetch_naukrigulf
    from scraper import fetch_indeed, fetch_linkedin

    return {
        "linkedin": fetch_linkedin,
        "indeed": fetch_indeed,
        "bayt": fetch_bayt,
        "naukrigulf": fetch_naukrigulf,
        "google_jobs": fetch_google_jobs,
        "linkedin_posts": fetch_linkedin_posts,
    }


def _default_driver_factory():
    from browser import create_driver
    from paths import linkedin_profile_dir

    return create_driver(headless=False, user_data_dir=str(linkedin_profile_dir()))


def _query_fields(query: dict) -> dict:
    return {
        "index": query["index"],
        "source": query["source"],
        "keyword": query["keyword"],
        "location_id": query["location_id"],
    }


def _driver_alive(driver) -> bool:
    try:
        driver.window_handles
        return True
    except Exception:
        return False


def _quit(driver) -> None:
    if driver is None:
        return
    try:
        driver.quit()
    except Exception:
        log.debug("driver.quit() failed", exc_info=True)


def _make_throttles(delays) -> dict:
    return {source: Throttle(*gap) for source, gap in delays.items()}


def _may_proceed(throttles, source, cancel) -> bool:
    """Wait out the polite gap for `source`; False means cancelled while waiting."""
    throttle = throttles.get(source)
    return True if throttle is None else throttle.wait(cancel)


def _skip_all(queries, reason, out) -> None:
    for query in queries:
        out.put(("skipped", query, reason))


def _http_lane(queries, fetchers, keywords, cancel, out, delays) -> None:
    seen_urls = set()
    throttles = _make_throttles(delays)
    for query in queries:
        if cancel.is_set():
            return
        if not _may_proceed(throttles, query["source"], cancel):
            return
        out.put(("started", query))
        try:
            jobs = fetchers[query["source"]](query["args"], seen_urls, keywords)
        except Exception as exc:
            log.exception("Query failed: %s %r", query["source"], query["args"])
            out.put(("failed", query, type(exc).__name__))
            continue
        out.put(("done", query, jobs))


def _browser_lane(queries, fetchers, driver_factory, keywords, cancel, out, delays) -> None:
    seen_urls = set()
    throttles = _make_throttles(delays)
    driver = None
    restarts_left = 1
    login_blocked = False
    try:
        if cancel.is_set():
            return
        try:
            driver = driver_factory()
        except ChromeNotFound:
            log.warning("Google Chrome not found; skipping browser sources")
            out.put(("event", {"type": "browser_failed", "reason": "chrome_not_found"}))
            _skip_all(queries, "browser_unavailable", out)
            return
        except Exception:
            log.exception("Could not start Chrome")
            out.put(("event", {"type": "browser_failed", "reason": "start_failed"}))
            _skip_all(queries, "browser_unavailable", out)
            return

        for i, query in enumerate(queries):
            if cancel.is_set():
                return
            if query["source"] == "linkedin_posts" and login_blocked:
                out.put(("skipped", query, "login_required"))
                continue
            if not _may_proceed(throttles, query["source"], cancel):
                return
            out.put(("started", query))
            try:
                jobs = fetchers[query["source"]](driver, query["args"], seen_urls, keywords)
            except LinkedInLoginRequired as exc:
                if cancel.is_set():
                    return  # Ctrl+C killed Chrome too; the failure is spurious
                log.warning("LinkedIn login required (%s); skipping post searches", exc)
                login_blocked = True
                out.put(("failed", query, "LinkedInLoginRequired"))
                out.put(("event", {"type": "login_required", "source": query["source"]}))
                continue
            except Exception as exc:
                if cancel.is_set():
                    return  # interrupted by cancel, not a real failure: no restart, no event
                log.exception("Query failed: %s %r", query["source"], query["args"])
                out.put(("failed", query, type(exc).__name__))
                if _driver_alive(driver):
                    continue
                _quit(driver)
                driver = None
                if restarts_left > 0:
                    restarts_left -= 1
                    try:
                        driver = driver_factory()
                        log.info("Chrome restarted after a crash")
                        continue
                    except Exception:
                        log.exception("Could not restart Chrome")
                out.put(("event", {"type": "browser_failed", "reason": "crashed"}))
                _skip_all(queries[i + 1:], "browser_unavailable", out)
                return
            out.put(("done", query, jobs))
    finally:
        _quit(driver)


def _join_lanes(lanes, cancel) -> None:
    """Wait for every lane to finish; Ctrl+C here cancels but never abandons a lane."""
    for lane in lanes:
        while lane.is_alive():
            try:
                lane.join()
            except KeyboardInterrupt:
                cancel.set()


def _make_emitter(on_event):
    def emit(event: dict) -> None:
        if on_event is None:
            return
        try:
            on_event(event)
        except Exception:
            log.exception("on_event raised for %s", event.get("type"))
        except KeyboardInterrupt:
            if event.get("type") != "run_finished":
                raise
    return emit


def run_search(settings: dict, on_event=None, cancel=None, *, country_labels=None,
               store=None, fetchers=None, driver_factory=None, delays=None) -> RunSummary:
    """
    Run one search. settings = {"keywords", "locations", "sources"}.

    Errors before the run starts (e.g. the database cannot be written) raise.
    After `run_started` it never raises: failures become events and the
    returned state is 'failed'. A KeyboardInterrupt on the calling thread
    cancels the run instead of aborting it.

    delays = {source: (min_seconds, max_seconds)} is the polite gap between
    requests to one site; None uses POLITE_DELAYS, and a source missing from it
    is not throttled.
    """
    cancel = cancel or threading.Event()
    owns_store = store is None
    store = store or Store()
    fetchers = fetchers or _default_fetchers()
    driver_factory = driver_factory or _default_driver_factory
    delays = POLITE_DELAYS if delays is None else delays
    emit = _make_emitter(on_event)
    keywords = list(settings["keywords"])

    order = {source: i for i, source in enumerate(SOURCE_ORDER)}
    queries = plan_queries(keywords, settings["locations"], settings["sources"], country_labels)
    queries.sort(key=lambda q: order[q["source"]])
    for i, query in enumerate(queries):
        query["index"] = i

    run_id = store.start_run({key: list(settings[key]) for key in ("keywords", "locations", "sources")})
    emit({"type": "run_started", "run_id": run_id, "total": len(queries)})

    http_queries = [q for q in queries if q["source"] in HTTP_SOURCES]
    browser_queries = [q for q in queries if q["source"] in BROWSER_SOURCES]
    out = queue.Queue()
    lanes = []
    if http_queries:
        lanes.append(threading.Thread(
            target=_http_lane, args=(http_queries, fetchers, keywords, cancel, out, delays),
            name="hawkeye-http-lane", daemon=True))
    if browser_queries:
        lanes.append(threading.Thread(
            target=_browser_lane, args=(browser_queries, fetchers, driver_factory, keywords, cancel, out, delays),
            name="hawkeye-browser-lane", daemon=True))
    for lane in lanes:
        lane.start()

    totals = {"found": 0, "new_jobs": []}

    def handle(message) -> None:
        kind = message[0]
        if kind == "started":
            emit({"type": "query_started", **_query_fields(message[1])})
        elif kind == "done":
            query, jobs = message[1], message[2]
            for job in jobs:
                job["keyword"] = query["keyword"]
                job["location_id"] = query["location_id"]
            fresh = store.add_jobs(run_id, jobs)
            totals["found"] += len(jobs)
            totals["new_jobs"].extend(fresh)
            emit({"type": "query_done", **_query_fields(query), "found": len(jobs), "new": len(fresh)})
        elif kind == "failed":
            emit({"type": "query_failed", **_query_fields(message[1]), "error": message[2]})
        elif kind == "skipped":
            emit({"type": "query_skipped", **_query_fields(message[1]), "reason": message[2]})
        elif kind == "event":
            emit(message[1])

    state = "done"
    try:
        while True:
            try:
                try:
                    message = out.get(timeout=_QUEUE_POLL_SECONDS)
                except queue.Empty:
                    if not any(lane.is_alive() for lane in lanes) and out.empty():
                        break
                    continue
                handle(message)
            except KeyboardInterrupt:
                log.info("Interrupted: cancelling after in-flight queries")
                cancel.set()
        state = "cancelled" if cancel.is_set() else "done"
    except Exception:
        log.exception("Search run %s failed", run_id)
        cancel.set()
        state = "failed"
    finally:
        _join_lanes(lanes, cancel)

    # Tail: a Ctrl+C here must not leave the run 'running' or skip run_finished.
    for _ in range(3):
        try:
            store.finish_run(run_id, state, totals["found"], len(totals["new_jobs"]))
            break
        except KeyboardInterrupt:
            cancel.set()
        except Exception:
            log.exception("Could not record the end of run %s", run_id)
            break
    emit({"type": "run_finished", "run_id": run_id, "state": state,
          "found": totals["found"], "new": len(totals["new_jobs"])})
    if owns_store:
        store.close()
    return RunSummary(run_id=run_id, state=state, found=totals["found"], new_jobs=totals["new_jobs"])
