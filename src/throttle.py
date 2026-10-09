"""
throttle.py — Polite spacing between requests to one site
==========================================================
Waiting for job cards to appear (instead of fixed sleeps) made searches several
times faster, but the old sleeps were also the only thing spacing out requests.
Hitting one site every second or two looks like a bot: LinkedIn can restrict the
user's own account, Google shows CAPTCHAs, Bayt serves Cloudflare challenges,
and JobSpy gets HTTP 429. Slower is fine; harming the user is not.

A Throttle keeps a random minimum gap between the START of consecutive requests
to one site (page-load time counts toward the gap). pipeline.py owns one per
host group (e.g. linkedin.com), shared by every lane that hits that host, and
calls wait() before every query; threads reserve slots under a lock so two lanes
never get slots closer than the gap. After a failed query pipeline.py calls
back_off(), which doubles the gap (capped at 4x). Delays are tuned in
pipeline.POLITE_DELAYS.
"""

import random
import threading
import time

_SLICE_SECONDS = 0.5
_MAX_BACKOFF = 4


class Throttle:
    """Keeps a random minimum gap between the START of consecutive requests to one site."""

    def __init__(self, min_seconds, max_seconds, clock=time.monotonic, sleep=time.sleep,
                 rand=random.uniform):
        self.min_seconds = min_seconds
        self.max_seconds = max_seconds
        self._clock = clock
        self._sleep = sleep
        self._rand = rand
        self._last_start = None  # start time of the most recently reserved slot
        self._factor = 1
        self._lock = threading.Lock()

    def back_off(self) -> None:
        """Double the gap for the rest of this Throttle's life, up to 4x the original."""
        with self._lock:
            self._factor = min(self._factor * 2, _MAX_BACKOFF)

    def wait(self, cancel=None) -> bool:
        """Block until this site may be hit again. Returns False if `cancel` was set
        while waiting (caller must stop), True otherwise. The first call never waits.
        Reserves the start time of the request it allows, so concurrent callers
        are spaced a full gap apart."""
        if cancel is not None and cancel.is_set():
            return False
        with self._lock:
            now = self._clock()
            if self._last_start is None:
                slot = now
            else:
                gap = self._rand(self.min_seconds, self.max_seconds) * self._factor
                slot = max(now, self._last_start + gap)
            self._last_start = slot
        while True:
            if cancel is not None and cancel.is_set():
                return False
            remaining = slot - self._clock()
            if remaining <= 0:
                return True
            self._sleep(min(_SLICE_SECONDS, remaining))
