import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

import run_lock
import paths


class RunLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "run.lock"

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_path_is_in_data_dir(self):
        self.assertEqual(run_lock.RunLock().path, paths.data_dir() / "run.lock")

    def test_acquire_and_release(self):
        lock = run_lock.RunLock(self.path)
        self.assertTrue(lock.acquire())
        self.assertTrue(lock.held)
        lock.release()
        self.assertFalse(lock.held)
        lock.release()  # harmless twice

    def test_second_holder_is_refused_until_release(self):
        first, second = run_lock.RunLock(self.path), run_lock.RunLock(self.path)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        first.release()
        self.assertTrue(second.acquire())
        second.release()

    def test_lock_held_by_other_process_is_refused(self):
        code = textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(Path(__file__).resolve().parents[1] / 'src')!r})
            import run_lock
            lock = run_lock.RunLock({str(self.path)!r})
            assert lock.acquire()
            print("locked", flush=True)
            time.sleep(30)
        """)
        child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "locked")
            self.assertFalse(run_lock.RunLock(self.path).acquire())
        finally:
            child.kill()
            child.wait()
            child.stdout.close()
        deadline = time.monotonic() + 5
        lock = run_lock.RunLock(self.path)
        while not lock.acquire():  # the OS frees the lock when the process dies
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.05)
        lock.release()


if __name__ == "__main__":
    unittest.main()
