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
source per lane and calls wait() before every query. Delays are tuned in
pipeline.POLITE_DELAYS.
"""

import random
import time

_SLICE_SECONDS = 0.5


class Throttle:
    """Keeps a random minimum gap between the START of consecutive requests to one site."""

    def __init__(self, min_seconds, max_seconds, clock=time.monotonic, sleep=time.sleep,
                 rand=random.uniform):
        self.min_seconds = min_seconds
        self.max_seconds = max_seconds
        self._clock = clock
        self._sleep = sleep
        self._rand = rand
        self._last_start = None

    def wait(self, cancel=None) -> bool:
        """Block until this site may be hit again. Returns False if `cancel` was set
        while waiting (caller must stop), True otherwise. The first call never waits.
        Records the start time of the request it allows."""
        if self._last_start is not None:
            gap = self._rand(self.min_seconds, self.max_seconds)
            ready_at = self._last_start + gap
            while True:
                if cancel is not None and cancel.is_set():
                    return False
                remaining = ready_at - self._clock()
                if remaining <= 0:
                    break
                self._sleep(min(_SLICE_SECONDS, remaining))
        self._last_start = self._clock()
        return True
