import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from throttle import Throttle


class FakeTime:
    """A clock that only moves when sleep() is called (or advance() by hand)."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []
        self.on_sleep = None

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep(len(self.sleeps))

    def advance(self, seconds):
        self.now += seconds


class ThrottleTests(unittest.TestCase):
    def make(self, low=5, high=5, rand=None):
        self.time = FakeTime()
        self.rand_calls = []

        def default_rand(a, b):
            self.rand_calls.append((a, b))
            return b

        return Throttle(low, high, clock=self.time.clock, sleep=self.time.sleep,
                        rand=rand or default_rand)

    def test_first_wait_returns_true_without_sleeping(self):
        throttle = self.make()
        self.assertTrue(throttle.wait())
        self.assertEqual(self.time.sleeps, [])

    def test_second_wait_sleeps_the_remaining_gap(self):
        throttle = self.make(5, 5)
        throttle.wait()
        self.time.advance(2)
        self.assertTrue(throttle.wait())
        self.assertAlmostEqual(sum(self.time.sleeps), 3)

    def test_gap_is_measured_from_request_start(self):
        throttle = self.make(5, 5)
        throttle.wait()
        self.time.advance(2)
        throttle.wait()          # allowed at t=5
        self.time.advance(1)
        throttle.wait()          # gap runs from t=5, so 4 s remain
        self.assertAlmostEqual(sum(self.time.sleeps), 3 + 4)

    def test_no_sleep_when_gap_already_elapsed(self):
        throttle = self.make(5, 5)
        throttle.wait()
        self.time.advance(5)
        self.assertTrue(throttle.wait())
        self.assertEqual(self.time.sleeps, [])

    def test_sleeps_in_slices_of_at_most_half_a_second(self):
        throttle = self.make(5, 5)
        throttle.wait()
        throttle.wait()
        self.assertTrue(all(0 < s <= 0.5 for s in self.time.sleeps))
        self.assertAlmostEqual(sum(self.time.sleeps), 5)

    def test_cancel_during_wait_returns_false_after_current_slice(self):
        throttle = self.make(30, 30)
        cancel = threading.Event()
        self.time.on_sleep = lambda n: cancel.set() if n == 3 else None
        throttle.wait(cancel)
        self.assertFalse(throttle.wait(cancel))
        self.assertEqual(len(self.time.sleeps), 3)

    def test_already_cancelled_does_not_sleep(self):
        throttle = self.make(30, 30)
        throttle.wait()
        cancel = threading.Event()
        cancel.set()
        self.assertFalse(throttle.wait(cancel))
        self.assertEqual(self.time.sleeps, [])

    def test_zero_gap_never_sleeps(self):
        throttle = self.make(0, 0)
        for _ in range(5):
            self.assertTrue(throttle.wait())
        self.assertEqual(self.time.sleeps, [])

    def test_gap_is_drawn_from_rand_with_min_and_max(self):
        throttle = self.make(3, 6)
        throttle.wait()
        throttle.wait()
        throttle.wait()
        self.assertEqual(self.rand_calls, [(3, 6), (3, 6)])

    def test_random_gap_changes_each_request(self):
        gaps = iter([2, 4])
        throttle = self.make(2, 4, rand=lambda a, b: next(gaps))
        throttle.wait()
        throttle.wait()
        throttle.wait()
        self.assertAlmostEqual(sum(self.time.sleeps), 6)


if __name__ == "__main__":
    unittest.main()
