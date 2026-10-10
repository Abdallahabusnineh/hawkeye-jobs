import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

import main
from pipeline import RunSummary
from store import job_id

SETTINGS = {"keywords": ["Java"], "locations": ["jo"], "sources": ["linkedin"]}
COUNTRIES = [{"id": "remote", "label": "Remote"}, {"id": "jo", "label": "Jordan"}]


class FormatEventTests(unittest.TestCase):
    def test_query_done_uses_site_and_country_names(self):
        line = main.format_event(
            {"type": "query_done", "index": 0, "source": "bayt", "keyword": "Java",
             "location_id": "jo", "found": 3, "new": 1},
            {"jo": "Jordan"},
        )
        self.assertEqual(line, "  [Bayt.com] 'Java' in Jordan: 3 found, 1 new")

    def test_query_started_prints_nothing(self):
        self.assertIsNone(main.format_event(
            {"type": "query_started", "index": 0, "source": "bayt", "keyword": "Java", "location_id": "jo"}))

    def test_failure_points_to_log(self):
        line = main.format_event(
            {"type": "query_failed", "index": 0, "source": "linkedin_posts", "keyword": "Java",
             "location_id": None, "error": "TimeoutException"})
        self.assertIn("TimeoutException", line)
        self.assertIn("hawkeye.log", line)
        self.assertNotIn(" in ", line.split(":")[0])

    def test_chrome_missing_is_explained(self):
        line = main.format_event({"type": "browser_failed", "reason": "chrome_not_found"})
        self.assertIn("Chrome", line)


class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        self.env = patch.dict(os.environ, {"HAWKEYE_DATA_DIR": str(Path(self.tmp.name) / "data")})
        self.env.start()
        self.patches = [
            patch("main.ROOT", self.root),
            patch("main._load_dotenv"),
            patch("main.setup_logging"),
            patch("main.collect_settings", return_value=SETTINGS),
            patch("main.load_countries", return_value=COUNTRIES),
            patch("sys.stdout", new_callable=io.StringIO),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()
        self.tmp.cleanup()

    def _search_adding(self, jobs, state="done"):
        """run_search stand-in that stores `jobs` the way a real run would."""
        def fake(chosen, on_event=None, country_labels=None, store=None):
            run_id = store.start_run(chosen)
            added = store.add_jobs(run_id, jobs) if jobs else []
            return RunSummary(run_id=run_id, state=state, found=len(jobs), new_jobs=added)
        return fake

    def _unnotified(self):
        from store import Store
        with Store() as st:
            return st.unnotified_jobs()

    def test_cancelled_run_still_emails_new_jobs(self):
        job = {"title": "Java Developer", "url": "https://a.com/1", "description": "Acme", "source": "Bayt"}
        with patch("main.run_search", side_effect=self._search_adding([job], "cancelled")), \
             patch("main.email_configured", return_value=True), \
             patch("main.send_email", return_value=True) as send:
            main.main()
        emailed = send.call_args.args[0]
        self.assertEqual([j["url"] for j in emailed], ["https://a.com/1"])
        self.assertEqual(self._unnotified(), [])

    def test_unreported_job_from_killed_run_is_emailed_and_marked(self):
        from store import Store
        with Store() as st:
            run_id = st.start_run(SETTINGS)
            st.add_jobs(run_id, [{"title": "Old", "url": "https://a.com/old", "description": "x", "source": "Bayt"}])
        with patch("main.run_search", side_effect=self._search_adding([])), \
             patch("main.email_configured", return_value=True), \
             patch("main.send_email", return_value=True) as send:
            main.main()
        self.assertEqual([j["url"] for j in send.call_args.args[0]], ["https://a.com/old"])
        self.assertEqual(self._unnotified(), [])

    def test_failed_email_keeps_jobs_unnotified_and_retries_next_run(self):
        job = {"title": "Java", "url": "https://a.com/1", "description": "x", "source": "Bayt"}
        with patch("main.run_search", side_effect=self._search_adding([job])), \
             patch("main.email_configured", return_value=True), \
             patch("main.send_email", return_value=False):
            main.main()
        self.assertEqual(len(self._unnotified()), 1)
        self.assertIn("will be included again next time", sys.stdout.getvalue())
        with patch("main.run_search", side_effect=self._search_adding([])), \
             patch("main.email_configured", return_value=True), \
             patch("main.send_email", return_value=True) as send:
            main.main()
        self.assertEqual([j["url"] for j in send.call_args.args[0]], ["https://a.com/1"])
        self.assertEqual(self._unnotified(), [])

    def test_without_email_setup_terminal_print_marks_jobs(self):
        job = {"title": "Java", "url": "https://a.com/1", "description": "x", "source": "Bayt"}
        with patch("main.run_search", side_effect=self._search_adding([job])), \
             patch("main.email_configured", return_value=False), \
             patch("main.send_email") as send:
            main.main()
        send.assert_not_called()
        self.assertIn("https://a.com/1", sys.stdout.getvalue())
        self.assertEqual(self._unnotified(), [])
        with patch("main.run_search", side_effect=self._search_adding([])), \
             patch("main.email_configured", return_value=False):
            main.main()
        self.assertIn("No new jobs to report", sys.stdout.getvalue())

    def test_no_new_jobs_sends_nothing(self):
        with patch("main.run_search", return_value=RunSummary(1, "done", 0, [])), \
             patch("main.email_configured", return_value=True), \
             patch("main.send_email") as send:
            main.main()
        send.assert_not_called()

    def test_legacy_seen_jobs_are_not_reported_again(self):
        old_url = "https://www.linkedin.com/jobs/view/111"
        (self.root / "seen_jobs.json").write_text(json.dumps({"seen": [job_id(old_url)], "count": 1}), encoding="utf-8")

        def fake_linkedin(args, seen_urls, keywords):
            return [
                {"title": "Java Developer", "url": old_url, "description": "Old", "source": "Linkedin"},
                {"title": "Java Developer", "url": "https://www.linkedin.com/jobs/view/222",
                 "description": "New", "source": "Linkedin"},
            ]

        with patch("pipeline._default_fetchers", return_value={"linkedin": fake_linkedin}), \
             patch("main.email_configured", return_value=True), \
             patch("main.send_email", return_value=True) as send:
            main.main()
        emailed = send.call_args.args[0]
        self.assertEqual([j["url"] for j in emailed], ["https://www.linkedin.com/jobs/view/222"])

    def test_legacy_config_copied_into_data_dir(self):
        (self.root / "config.json").write_text(json.dumps(SETTINGS), encoding="utf-8")
        with patch("main.run_search", return_value=RunSummary(1, "done", 0, [])), patch("main.send_email"):
            main.main()
        self.assertTrue((Path(os.environ["HAWKEYE_DATA_DIR"]) / "config.json").exists())


if __name__ == "__main__":
    unittest.main()
