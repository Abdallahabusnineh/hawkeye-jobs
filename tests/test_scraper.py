import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

import scraper

ROWS = [
    {"job_url": "https://www.linkedin.com/jobs/view/1?utm_source=x", "title": "Java Developer",
     "date_posted": None, "company": "Acme", "location": "Amman, JO", "site": "linkedin"},
    {"job_url": "https://www.linkedin.com/jobs/view/2", "title": "Chef",
     "date_posted": None, "company": "Hotel", "location": "Amman", "site": "linkedin"},
    {"job_url": "https://www.linkedin.com/jobs/view/3", "title": "Java Engineer",
     "date_posted": "2020-01-01", "company": "Old", "location": "Amman", "site": "linkedin"},
]


class FetchLinkedInTests(unittest.TestCase):
    def test_parses_filters_and_passes_args(self):
        import utils
        utils.set_job_age_days(7)
        self.addCleanup(utils.set_job_age_days, utils.DEFAULT_JOB_AGE_DAYS)
        with patch("scraper.scrape_jobs", return_value=pd.DataFrame(ROWS)) as scrape_jobs:
            jobs = scraper.fetch_linkedin({"term": "Java", "location": "Jordan"}, set(), ["Java Developer"])
        self.assertEqual([j["url"] for j in jobs], ["https://www.linkedin.com/jobs/view/1"])
        self.assertEqual(jobs[0]["description"], "Acme - Amman, JO")
        self.assertEqual(jobs[0]["source"], "Linkedin")
        self.assertEqual(jobs[0]["location"], "Jordan")
        kwargs = scrape_jobs.call_args.kwargs
        self.assertEqual(kwargs["site_name"], ["linkedin"])
        self.assertEqual((kwargs["search_term"], kwargs["location"]), ("Java", "Jordan"))
        self.assertEqual((kwargs["results_wanted"], kwargs["hours_old"]), (10, 168))

    def test_job_age_setting_reaches_jobspy(self):
        import utils
        utils.set_job_age_days(30)
        self.addCleanup(utils.set_job_age_days, utils.DEFAULT_JOB_AGE_DAYS)
        with patch("scraper.scrape_jobs", return_value=pd.DataFrame([])) as scrape_jobs:
            scraper.fetch_linkedin({"term": "Java", "location": "Jordan"}, set(), ["Java"])
        self.assertEqual(scrape_jobs.call_args.kwargs["hours_old"], 720)

    def test_skips_urls_already_seen_in_this_lane(self):
        seen = {"https://www.linkedin.com/jobs/view/1"}
        with patch("scraper.scrape_jobs", return_value=pd.DataFrame(ROWS)):
            self.assertEqual(scraper.fetch_linkedin({"term": "Java", "location": "Jordan"}, seen, ["Java"]), [])

    def test_errors_propagate(self):
        with patch("scraper.scrape_jobs", side_effect=RuntimeError("429 Too Many Requests")):
            with self.assertRaises(RuntimeError):
                scraper.fetch_linkedin({"term": "Java", "location": "Jordan"}, set(), ["Java"])


class FetchIndeedTests(unittest.TestCase):
    def test_passes_country(self):
        with patch("scraper.scrape_jobs", return_value=pd.DataFrame([])) as scrape_jobs:
            jobs = scraper.fetch_indeed(
                {"term": "Java", "location": "United Arab Emirates", "country": "united arab emirates"},
                set(), ["Java"],
            )
        self.assertEqual(jobs, [])
        kwargs = scrape_jobs.call_args.kwargs
        self.assertEqual(kwargs["site_name"], ["indeed"])
        self.assertEqual(kwargs["country_indeed"], "united arab emirates")


if __name__ == "__main__":
    unittest.main()
