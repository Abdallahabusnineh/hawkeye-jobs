import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import bayt
import naukrigulf

FIXTURES = Path(__file__).resolve().parent / "fixtures"
KEYWORDS = ["Java Developer"]


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeDriver:
    """Just enough of a Selenium driver for the fetch_* functions."""

    def __init__(self, html="", title="Page", current_url="https://example.com/", fail_on_get=None):
        self.page_source = html
        self.title = title
        self.current_url = current_url
        self.visited = []
        self.scripts = []
        self.window_handles = ["main"]
        self.switch_to = SimpleNamespace(window=lambda handle: None)
        self.fail_on_get = fail_on_get

    def get(self, url):
        if self.fail_on_get:
            raise self.fail_on_get
        self.visited.append(url)

    def close(self):
        pass

    def execute_script(self, script):
        self.scripts.append(script)


class BaytTests(unittest.TestCase):
    def test_fetch_parses_relevant_recent_jobs(self):
        driver = FakeDriver(fixture("bayt.html"))
        with patch("bayt.wait_for", return_value=True) as wait:
            jobs = bayt.fetch_bayt(driver, ("java-developer", "jordan"), set(), KEYWORDS)
        self.assertEqual(driver.visited, ["https://www.bayt.com/en/jordan/jobs/java-developer-jobs/"])
        wait.assert_called_once_with(driver, "li[data-job-id]", 20)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["title"], "Senior Java Developer")
        self.assertEqual(job["url"], "https://www.bayt.com/en/jordan/jobs/senior-java-developer-101/")
        self.assertEqual(job["description"], "Acme Software - Amman, Jordan")
        self.assertEqual((job["source"], job["location"], job["published"]), ("Bayt", "jordan", "Yesterday"))

    def test_warns_when_nothing_loaded(self):
        driver = FakeDriver("<html></html>", title="Just a moment...")
        with patch("bayt.wait_for", return_value=False), self.assertLogs("bayt", "WARNING") as logs:
            self.assertEqual(bayt.fetch_bayt(driver, ("java", "jordan"), set(), KEYWORDS), [])
        self.assertIn("Just a moment", logs.output[0])

    def test_navigation_errors_propagate(self):
        driver = FakeDriver(fail_on_get=RuntimeError("net::ERR_TIMED_OUT"))
        with patch("bayt.wait_for", return_value=True):
            with self.assertRaises(RuntimeError):
                bayt.fetch_bayt(driver, ("java", "jordan"), set(), KEYWORDS)


class NaukriGulfTests(unittest.TestCase):
    def test_fetch_parses_relevant_recent_jobs(self):
        driver = FakeDriver(fixture("naukrigulf.html"))
        with patch("naukrigulf.wait_for", return_value=True) as wait:
            jobs = naukrigulf.fetch_naukrigulf(driver, ("java developer", "saudi-arabia"), set(), KEYWORDS)
        self.assertEqual(driver.visited, ["https://www.naukrigulf.com/java-developer-jobs-in-saudi-arabia"])
        wait.assert_called_once_with(driver, ".ng-box.srp-tuple", 15)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["title"], "Java Developer")
        self.assertEqual(job["description"], "Acme Gulf - Riyadh - Saudi Arabia")
        self.assertEqual((job["source"], job["published"]), ("NaukriGulf", "1 day ago"))

    def test_seen_urls_are_skipped(self):
        driver = FakeDriver(fixture("naukrigulf.html"))
        seen = {"https://www.naukrigulf.com/java-developer-jobs-in-riyadh-saudi-arabia-in-acme-gulf-5-to-8-years-n-cd-111"}
        with patch("naukrigulf.wait_for", return_value=True):
            self.assertEqual(naukrigulf.fetch_naukrigulf(driver, ("java developer", "saudi-arabia"), seen, KEYWORDS), [])


if __name__ == "__main__":
    unittest.main()
