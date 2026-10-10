import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

from store import Store, job_id


def make_job(url, title="Java Developer", source="Bayt", **extra):
    job = {
        "title": title,
        "url": url,
        "description": "Acme - Amman",
        "source": source,
        "keyword": "Java",
        "location": "jordan",
        "location_id": "jo",
        "published": "1 day ago",
    }
    job.update(extra)
    return job


SETTINGS = {"keywords": ["Java"], "locations": ["jo"], "sources": ["bayt"]}


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        self.store = Store(self.db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()


class JobIdTests(unittest.TestCase):
    def test_matches_old_tracker_hash(self):
        url = "https://www.bayt.com/en/jordan/jobs/java-123"
        self.assertEqual(job_id(url), hashlib.md5(url.encode()).hexdigest())

    def test_ignores_tracking_params_and_trailing_slash(self):
        base = job_id("https://indeed.com/viewjob?jk=abc")
        self.assertEqual(job_id("https://indeed.com/viewjob?jk=abc&utm_source=google"), base)
        self.assertEqual(job_id("https://x.com/a/"), job_id("https://x.com/a"))


class SchemaTests(StoreTestCase):
    def test_schema_version_and_wal(self):
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        conn.close()

    def test_reopening_existing_db_keeps_data(self):
        run_id = self.store.start_run(SETTINGS)
        self.store.add_jobs(run_id, [make_job("https://a.com/1")])
        self.store.close()
        self.store = Store(self.db)
        self.assertEqual(len(self.store.list_jobs()), 1)


class AddJobsTests(StoreTestCase):
    def test_returns_only_new_jobs(self):
        run1 = self.store.start_run(SETTINGS)
        first = self.store.add_jobs(run1, [make_job("https://a.com/1"), make_job("https://a.com/2")])
        self.assertEqual(len(first), 2)
        self.assertEqual(first[0]["id"], job_id("https://a.com/1"))
        run2 = self.store.start_run(SETTINGS)
        second = self.store.add_jobs(run2, [make_job("https://a.com/2"), make_job("https://a.com/3")])
        self.assertEqual([j["url"] for j in second], ["https://a.com/3"])

    def test_reseen_job_keeps_status_and_updates_last_seen(self):
        run1 = self.store.start_run(SETTINGS)
        with patch("store._now", return_value="2026-01-01T00:00:00Z"):
            self.store.add_jobs(run1, [make_job("https://a.com/1")])
        self.store.set_status(job_id("https://a.com/1"), "saved")
        run2 = self.store.start_run(SETTINGS)
        with patch("store._now", return_value="2026-01-02T00:00:00Z"):
            self.assertEqual(self.store.add_jobs(run2, [make_job("https://a.com/1")]), [])
        row = self.store.list_jobs()[0]
        self.assertEqual(row["status"], "saved")
        self.assertEqual(row["first_seen_at"], "2026-01-01T00:00:00Z")
        self.assertEqual(row["last_seen_at"], "2026-01-02T00:00:00Z")
        self.assertEqual(row["first_run_id"], run1)

    def test_duplicate_in_same_batch_is_new_once(self):
        run_id = self.store.start_run(SETTINGS)
        new = self.store.add_jobs(run_id, [
            make_job("https://a.com/1"),
            make_job("https://a.com/1?utm_source=x"),
        ])
        self.assertEqual(len(new), 1)
        self.assertEqual(len(self.store.list_jobs()), 1)

    def test_jobs_without_url_are_ignored(self):
        run_id = self.store.start_run(SETTINGS)
        self.assertEqual(self.store.add_jobs(run_id, [make_job("")]), [])
        self.assertEqual(self.store.list_jobs(), [])

    def test_stores_keyword_and_location_id(self):
        run_id = self.store.start_run(SETTINGS)
        self.store.add_jobs(run_id, [make_job("https://a.com/1", keyword="Kotlin", location_id="ae")])
        row = self.store.list_jobs()[0]
        self.assertEqual((row["keyword"], row["location_id"], row["status"]), ("Kotlin", "ae", "new"))


class LegacySeenTests(StoreTestCase):
    def test_legacy_hash_blocks_job_and_import_is_idempotent(self):
        legacy = Path(self.tmp.name) / "seen_jobs.json"
        legacy.write_text(json.dumps({"seen": [job_id("https://a.com/old")], "count": 1}), encoding="utf-8")
        self.assertEqual(self.store.import_legacy_seen_file(legacy), 1)
        self.assertEqual(self.store.import_legacy_seen_file(legacy), 0)
        run_id = self.store.start_run(SETTINGS)
        new = self.store.add_jobs(run_id, [make_job("https://a.com/old"), make_job("https://a.com/fresh")])
        self.assertEqual([j["url"] for j in new], ["https://a.com/fresh"])

    def test_missing_or_corrupt_file_imports_nothing(self):
        self.assertEqual(self.store.import_legacy_seen_file(Path(self.tmp.name) / "nope.json"), 0)
        bad = Path(self.tmp.name) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with self.assertLogs("store", "WARNING"):
            self.assertEqual(self.store.import_legacy_seen_file(bad), 0)

    def test_non_utf8_file_imports_nothing(self):
        bad = Path(self.tmp.name) / "latin.json"
        bad.write_bytes(b'{"seen": ["\xff\xfe"]}')
        with self.assertLogs("store", "WARNING"):
            self.assertEqual(self.store.import_legacy_seen_file(bad), 0)

    def test_seen_must_be_a_list(self):
        bad = Path(self.tmp.name) / "str.json"
        bad.write_text(json.dumps({"seen": "abc"}), encoding="utf-8")
        self.assertEqual(self.store.import_legacy_seen_file(bad), 0)


class StatusAndListTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.run_id = self.store.start_run(SETTINGS)
        self.store.add_jobs(self.run_id, [
            make_job("https://a.com/1", title="Java Developer", source="Bayt"),
            make_job("https://a.com/2", title="Kotlin 100% Remote", source="Linkedin"),
            make_job("https://a.com/3", title="Java_Lead", source="Bayt"),
        ])

    def test_set_status_rejects_unknown_value(self):
        with self.assertRaises(ValueError):
            self.store.set_status(job_id("https://a.com/1"), "archived")

    def test_set_status_on_unknown_job_returns_false(self):
        self.assertFalse(self.store.set_status("nope", "saved"))
        self.assertTrue(self.store.set_status(job_id("https://a.com/1"), "applied"))

    def test_dismissed_hidden_by_default(self):
        self.store.set_status(job_id("https://a.com/1"), "dismissed")
        urls = {j["url"] for j in self.store.list_jobs()}
        self.assertEqual(urls, {"https://a.com/2", "https://a.com/3"})
        self.assertEqual(len(self.store.list_jobs(include_dismissed=True)), 3)
        self.assertEqual([j["url"] for j in self.store.list_jobs(status="dismissed")], ["https://a.com/1"])

    def test_source_and_run_filters(self):
        self.assertEqual(len(self.store.list_jobs(source="Bayt")), 2)
        self.assertEqual(len(self.store.list_jobs(run_id=self.run_id)), 3)
        self.assertEqual(self.store.list_jobs(run_id=self.run_id + 1), [])

    def test_text_filter_is_literal(self):
        self.assertEqual([j["url"] for j in self.store.list_jobs(text="100%")], ["https://a.com/2"])
        self.assertEqual([j["url"] for j in self.store.list_jobs(text="a_L")], ["https://a.com/3"])
        self.assertEqual(self.store.list_jobs(text="%"), self.store.list_jobs(text="100%"))

    def test_newest_first(self):
        run2 = self.store.start_run(SETTINGS)
        with patch("store._now", return_value="2999-01-01T00:00:00Z"):
            self.store.add_jobs(run2, [make_job("https://a.com/latest")])
        self.assertEqual(self.store.list_jobs()[0]["url"], "https://a.com/latest")

    def test_list_jobs_rejects_unknown_status(self):
        with self.assertRaises(ValueError):
            self.store.list_jobs(status="archived")


class RunTests(StoreTestCase):
    def test_finish_run_and_list_runs(self):
        run_id = self.store.start_run(SETTINGS)
        self.store.finish_run(run_id, "done", found=5, new=2)
        runs = self.store.list_runs()
        self.assertEqual(runs[0]["id"], run_id)
        self.assertEqual(runs[0]["state"], "done")
        self.assertEqual((runs[0]["found_count"], runs[0]["new_count"]), (5, 2))
        self.assertEqual(runs[0]["settings"], SETTINGS)
        self.assertIsNotNone(runs[0]["finished_at"])

    def test_finish_run_rejects_bad_state(self):
        run_id = self.store.start_run(SETTINGS)
        with self.assertRaises(ValueError):
            self.store.finish_run(run_id, "running", 0, 0)

    def test_recover_interrupted_runs(self):
        stuck = self.store.start_run(SETTINGS)
        finished = self.store.start_run(SETTINGS)
        self.store.finish_run(finished, "done", 0, 0)
        self.assertEqual(self.store.recover_interrupted_runs(), 1)
        states = {r["id"]: r["state"] for r in self.store.list_runs()}
        self.assertEqual(states, {stuck: "failed", finished: "done"})


class ConcurrencyTests(StoreTestCase):
    def test_second_connection_sees_writes(self):
        reader = Store(self.db)
        try:
            run_id = self.store.start_run(SETTINGS)
            self.store.add_jobs(run_id, [make_job("https://a.com/1")])
            self.assertEqual(len(reader.list_jobs()), 1)
        finally:
            reader.close()


if __name__ == "__main__":
    unittest.main()
