import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401

from web import services


class BaseCountriesCacheTests(unittest.TestCase):
    def test_concurrent_first_calls_load_once(self):
        calls = []

        def slow_loader():
            calls.append(1)
            time.sleep(0.2)
            return [{"id": "jo"}]

        import countries
        with mock.patch.object(countries, "load_countries", slow_loader):
            fn = services._base_countries_factory()
            results = []
            threads = [threading.Thread(target=lambda: results.append(fn())) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(calls), 1)
        self.assertEqual(results, [[{"id": "jo"}], [{"id": "jo"}]])


if __name__ == "__main__":
    unittest.main()
