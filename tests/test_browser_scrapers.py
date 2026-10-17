import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

import bayt
import google_jobs
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
        self.assertEqual(driver.visited, ["https://www.bayt.com/en/jordan/jobs/java-developer-jobs/?options%5Bsort%5D%5B%5D=d"])
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
        self.assertEqual(driver.visited, ["https://www.naukrigulf.com/java-developer-jobs-in-saudi-arabia?sort=date"])
        wait.assert_called_once_with(driver, ".ng-box.srp-tuple", 15)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["title"], "Java Developer")
        self.assertEqual(job["description"], "Acme Gulf - Riyadh - Saudi Arabia")
        self.assertEqual((job["source"], job["published"]), ("NaukriGulf", "1 day ago"))

    def test_build_url_sorts_by_date_with_and_without_location(self):
        self.assertEqual(naukrigulf._build_url("java developer", "uae"),
                         "https://www.naukrigulf.com/java-developer-jobs-in-uae?sort=date")
        self.assertEqual(naukrigulf._build_url("java developer", "gulf-region"),
                         "https://www.naukrigulf.com/java-developer-jobs?sort=date")
        self.assertEqual(naukrigulf._build_url("java developer", ""),
                         "https://www.naukrigulf.com/java-developer-jobs?sort=date")

    def test_seen_urls_are_skipped(self):
        driver = FakeDriver(fixture("naukrigulf.html"))
        seen = {"https://www.naukrigulf.com/java-developer-jobs-in-riyadh-saudi-arabia-in-acme-gulf-5-to-8-years-n-cd-111"}
        with patch("naukrigulf.wait_for", return_value=True):
            self.assertEqual(naukrigulf.fetch_naukrigulf(driver, ("java developer", "saudi-arabia"), seen, KEYWORDS), [])


class GoogleJobsTests(unittest.TestCase):
    def test_fetch_takes_first_non_google_apply_link(self):
        driver = FakeDriver(fixture("google_jobs.html"))
        with patch("google_jobs.wait_for", return_value=True) as wait:
            jobs = google_jobs.fetch_google_jobs(driver, ("Java Developer", "UAE"), set(), KEYWORDS)
        self.assertEqual(driver.visited, ["https://www.google.com/search?q=Java+Developer+jobs+UAE&ibp=htl;jobs&hl=en"])
        wait.assert_called_once_with(driver, ".PUpOsf", 15)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["url"], "https://ae.linkedin.com/jobs/view/java-developer-at-acme-cloud-123")
        self.assertEqual(job["description"], "Acme Cloud - Dubai")
        self.assertEqual((job["source"], job["location"], job["published"]), ("Google Jobs", "UAE", ""))

    def test_fetch_uses_google_goto_redirect_links(self):
        driver = FakeDriver(fixture("google_jobs_goto.html"))
        with patch("google_jobs.wait_for", return_value=True):
            jobs = google_jobs.fetch_google_jobs(driver, ("Java Developer", "UAE"), set(), KEYWORDS)
        self.assertEqual([j["url"] for j in jobs], [
            "https://www.google.com/goto?url=CAESAcmeJavaLinkedInToken123",
            "https://www.google.com/goto?url=CAESDesertJavaToken789&hl=en",
        ])
        self.assertEqual(jobs[0]["description"], "Acme Cloud - Dubai (via LinkedIn)")
        self.assertEqual(jobs[1]["description"], "Desert Labs - Abu Dhabi (via Indeed)")

    def test_absolute_links_win_over_goto_links(self):
        html = (
            '<div class="PUpOsf">Java Developer</div><div class="a3jPc">Acme</div>'
            '<div class="FqK3wc">Dubai</div><div role="list" class="EDblX">'
            '<a href="/goto?url=CAESfirst" title="Apply on LinkedIn">x</a>'
            '<a href="https://careers.example.com/java" title="Apply on Acme Careers">y</a></div>'
        )
        jobs = google_jobs._parse_google_page(html, set(), "UAE", KEYWORDS)
        self.assertEqual(jobs[0]["url"], "https://careers.example.com/java")
        self.assertEqual(jobs[0]["description"], "Acme - Dubai (via Acme Careers)")

    def test_card_with_only_google_links_is_skipped(self):
        html = (
            '<div class="PUpOsf">Java Developer</div><div class="a3jPc">Acme</div>'
            '<div class="FqK3wc">Dubai</div><div role="list" class="EDblX">'
            '<a href="https://www.google.com/search?q=acme">Google</a></div>'
        )
        self.assertEqual(google_jobs._parse_google_page(html, set(), "UAE", KEYWORDS), [])

    def test_closes_extra_tabs(self):
        driver = FakeDriver(fixture("google_jobs.html"))
        driver.window_handles = ["main", "popup"]
        switched = []
        driver.switch_to = SimpleNamespace(window=switched.append)
        with patch("google_jobs.wait_for", return_value=True):
            google_jobs.fetch_google_jobs(driver, ("Java Developer", "UAE"), set(), KEYWORDS)
        self.assertEqual(switched, ["popup", "main"])


    def test_warns_when_nothing_loaded(self):
        driver = FakeDriver("<html></html>", title="Just a moment...")
        with patch("google_jobs.wait_for", return_value=False), self.assertLogs("google_jobs", "WARNING") as logs:
            self.assertEqual(google_jobs.fetch_google_jobs(driver, ("Java Developer", "UAE"), set(), KEYWORDS), [])
        self.assertIn("Just a moment", logs.output[0])
        self.assertIn(driver.visited[0], logs.output[0])

if __name__ == "__main__":
    unittest.main()
