"""
runner.py — The only way the desktop app starts a search
=========================================================
RunManager guarantees one search at a time — an in-process lock for the app's
own threads plus RunLock shared with terminal mode — and runs
pipeline.run_search on a worker thread. While it runs it:
  * records each finished query in run_queries (History page),
  * keeps a small `current` progress snapshot for /api/state,
  * fans every event out to SSE subscribers (bounded queues; a subscriber
    that falls behind is dropped and the UI reconnects),
  * enforces "LinkedIn posts at most once per 24 h" (the user's own account).
After the run it reports new jobs through the notifier (reporting.py).
AutoSearch (below) starts runs on a timer through the same RunManager.
"""

import logging
import queue
import threading
import time
from datetime import datetime, timezone

from run_lock import RunLock
from reporting import report_pending
from settings import load_app_settings, load_config, save_app_settings
from store import Store

log = logging.getLogger(__name__)

POSTS_SOURCE = "linkedin_posts"
POSTS_INTERVAL_SECONDS = 24 * 3600
_SUBSCRIBER_QUEUE_SIZE = 1000
_FINISHED_QUERY = ("query_done", "query_failed", "query_skipped")


class RunInProgress(RuntimeError):
    """A search is already running (in this app or in terminal mode)."""


class NoSearchSettings(RuntimeError):
    """The user has not set up a search yet."""


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value) -> float | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _default_country_labels() -> dict:
    from countries import CountryFetchError, load_countries
    try:
        return {c["id"]: c["label"] for c in load_countries()}
    except CountryFetchError:
        return {}


class RunManager:
    def __init__(self, *, notifier, run_search=None, store_factory=Store, lock=None,
                 settings_loader=load_config, app_settings_loader=load_app_settings,
                 app_settings_saver=save_app_settings, country_labels_loader=None, clock=time.time):
        if run_search is None:
            from pipeline import run_search
        self._notifier = notifier
        self._run_search = run_search
        self._store_factory = store_factory
        self._run_lock = lock or RunLock()
        self._settings_loader = settings_loader
        self._app_settings_loader = app_settings_loader
        self._app_settings_saver = app_settings_saver
        self._country_labels_loader = country_labels_loader or _default_country_labels
        self._clock = clock
        self._state_lock = threading.Lock()
        self._subscribers_lock = threading.Lock()
        self._subscribers = set()
        self._worker = None
        self._cancel = None
        self._last_posts_run = None
        self._current = None

    # ── subscribers ─────────────────────────────────────────────────────

    def subscribe(self, maxsize: int = _SUBSCRIBER_QUEUE_SIZE) -> queue.Queue:
        q = queue.Queue(maxsize=maxsize)
        with self._subscribers_lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q) -> None:
        with self._subscribers_lock:
            self._subscribers.discard(q)

    def is_subscribed(self, q) -> bool:
        with self._subscribers_lock:
            return q in self._subscribers

    def close_subscribers(self) -> None:
        with self._subscribers_lock:
            subscribers, self._subscribers = self._subscribers, set()
        for q in subscribers:
            try:
                q.put_nowait(None)
            except queue.Full:
                pass

    def _publish(self, event: dict) -> None:
        with self._subscribers_lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                log.info("Dropping a slow event subscriber")
                self.unsubscribe(q)

    # ── state ───────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._worker is not None and self._worker.is_alive()

    @property
    def current(self):
        with self._state_lock:
            return dict(self._current) if self._current else None

    def wait(self, timeout=None) -> bool:
        worker = self._worker
        if worker is None:
            return True
        worker.join(timeout)
        return not worker.is_alive()

    def cancel(self) -> bool:
        with self._state_lock:
            if self._worker is None or not self._worker.is_alive():
                return False
            self._cancel.set()
            return True

    # ── running ─────────────────────────────────────────────────────────

    def start(self, trigger: str = "manual"):
        """Start a search in the background and return its run id (None if it failed before starting)."""
        with self._state_lock:
            if self._worker is not None and self._worker.is_alive():
                raise RunInProgress("a search is already running in the app")
            if not self._run_lock.acquire():
                raise RunInProgress("a search is already running in another window or the terminal")
            try:
                settings = self._settings_loader()
                if not settings:
                    raise NoSearchSettings("no search settings saved")
                settings, posts_skipped = self._apply_posts_gate(dict(settings))
                self._cancel = threading.Event()
                started = threading.Event()
                holder = {}
                self._worker = threading.Thread(target=self._run, args=(settings, trigger, started, holder),
                                                name="hawkeye-run", daemon=True)
                self._worker.start()
            except BaseException:
                self._worker = None
                self._run_lock.release()
                raise
        if posts_skipped:
            self._publish({"type": "posts_skipped", "reason": "daily_limit"})
        started.wait(timeout=10)
        return holder.get("run_id")

    def _apply_posts_gate(self, settings: dict):
        if POSTS_SOURCE not in settings.get("sources", []):
            return settings, False
        last = _parse_iso(self._app_settings_loader().get("last_posts_run"))
        if self._last_posts_run is not None and (last is None or self._last_posts_run > last):
            last = self._last_posts_run
        if last is not None and self._clock() - last < POSTS_INTERVAL_SECONDS:
            settings["sources"] = [s for s in settings["sources"] if s != POSTS_SOURCE]
            return settings, True
        return settings, False

    def _run(self, settings, trigger, started, holder) -> None:
        store = None
        posts_marked = False
        try:
            store = self._store_factory()
            labels = self._country_labels_loader()

            def on_event(event: dict) -> None:
                nonlocal posts_marked
                kind = event["type"]
                if kind == "run_started":
                    holder["run_id"] = event["run_id"]
                    with self._state_lock:
                        self._current = {"run_id": event["run_id"], "trigger": trigger, "total": event["total"],
                                         "done": 0, "found": 0, "new": 0}
                    started.set()
                elif kind in _FINISHED_QUERY:
                    try:
                        store.record_query(holder["run_id"], event)
                    except Exception:
                        log.exception("Could not record query history")
                    with self._state_lock:
                        if self._current:
                            self._current["done"] += 1
                            self._current["found"] += int(event.get("found") or 0)
                            self._current["new"] += int(event.get("new") or 0)
                elif kind == "query_started" and event.get("source") == POSTS_SOURCE and not posts_marked:
                    posts_marked = True
                    now = self._clock()
                    self._last_posts_run = now
                    try:
                        self._app_settings_saver({"last_posts_run": _iso(now)})
                    except Exception:
                        log.exception("Could not save last_posts_run")
                self._publish({**event, "trigger": trigger})

            self._run_search(settings, on_event=on_event, cancel=self._cancel, country_labels=labels,
                             store=store, trigger=trigger)
            result = report_pending(store, self._notifier)
            self._publish({"type": "jobs_reported", "count": result.reported, "more_waiting": result.more_waiting})
        except Exception:
            log.exception("Search run crashed")
            self._publish({"type": "run_error"})
        finally:
            started.set()
            try:
                if store is not None:
                    store.close()
            finally:
                try:
                    with self._state_lock:
                        self._current = None
                finally:
                    self._run_lock.release()

    def report_pending_now(self) -> int:
        """Report jobs left unreported (e.g. the app was killed mid-run). Skipped while a search runs."""
        if self.is_running:
            return 0
        with self._store_factory() as store:
            result = report_pending(store, self._notifier)
        if result.jobs:
            self._publish({"type": "jobs_reported", "count": result.reported, "more_waiting": result.more_waiting})
        return result.reported
