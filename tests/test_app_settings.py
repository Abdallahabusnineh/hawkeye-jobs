import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

import settings
from version import __version__


class VersionTests(unittest.TestCase):
    def test_version_is_semver(self):
        parts = __version__.split(".")
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(p.isdigit() for p in parts))


class AppSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_when_file_missing(self):
        app = settings.load_app_settings(self.path)
        self.assertEqual(app["language"], "ar")
        self.assertEqual(app["theme"], "auto")
        self.assertEqual(app["auto_search"], {"enabled": True, "hours": 6})
        self.assertFalse(app["onboarding_done"])
        self.assertTrue(app["update_check"])
        self.assertNotIn("last_posts_run", app)  # LinkedIn posts removed in 1.2.0
        self.assertEqual(app["job_age_days"], 14)

    def test_job_age_accepts_only_the_choices(self):
        for days in (7, 14, 30):
            self.assertEqual(settings.save_app_settings({"job_age_days": days}, self.path)["job_age_days"], days)
        for bad in (10, 0, "14", True, None):
            with self.assertRaises(settings.SettingsError) as ctx:
                settings.save_app_settings({"job_age_days": bad}, self.path)
            self.assertEqual(ctx.exception.errors, {"job_age_days": "invalid"})

    def test_invalid_stored_values_fall_back_to_defaults(self):
        self.path.write_text(json.dumps({"app": {"language": "fr", "auto_search": {"enabled": "yes", "hours": 5}}}),
                             encoding="utf-8")
        app = settings.load_app_settings(self.path)
        self.assertEqual(app["language"], "ar")
        self.assertEqual(app["auto_search"], {"enabled": True, "hours": 6})

    def test_save_merges_and_keeps_search_keys(self):
        self.path.write_text(json.dumps({"keywords": ["Java"], "locations": ["jo"], "sources": ["bayt"]}),
                             encoding="utf-8")
        app = settings.save_app_settings({"language": "en", "auto_search": {"enabled": False, "hours": 12}}, self.path)
        self.assertEqual(app["language"], "en")
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(stored["keywords"], ["Java"])
        self.assertEqual(stored["app"]["auto_search"], {"enabled": False, "hours": 12})
        self.assertEqual(settings.load_app_settings(self.path)["theme"], "auto")

    def test_save_rejects_bad_values_with_field_errors(self):
        with self.assertRaises(settings.SettingsError) as ctx:
            settings.save_app_settings({"language": "fr", "auto_search": {"enabled": True, "hours": 5},
                                        "nonsense": 1}, self.path)
        self.assertEqual(ctx.exception.errors, {"language": "invalid", "auto_search": "invalid",
                                                "nonsense": "unknown"})
        self.assertFalse(self.path.exists())

    def test_old_last_posts_run_is_ignored_and_dropped_on_save(self):
        self.path.write_text(json.dumps({"app": {"last_posts_run": "2026-10-10T08:00:00Z"}}), encoding="utf-8")
        self.assertNotIn("last_posts_run", settings.load_app_settings(self.path))
        settings.save_app_settings({"theme": "dark"}, self.path)
        self.assertNotIn("last_posts_run", json.loads(self.path.read_text(encoding="utf-8"))["app"])

    def test_saved_search_drops_the_removed_linkedin_posts_source(self):
        self.path.write_text(json.dumps({"keywords": ["Flutter"], "locations": ["jo"],
                                         "sources": ["bayt", "linkedin_posts"]}), encoding="utf-8")
        self.assertEqual(settings.load_config(self.path)["sources"], ["bayt"])
        self.path.write_text(json.dumps({"keywords": ["Flutter"], "locations": ["jo"],
                                         "sources": ["linkedin_posts"]}), encoding="utf-8")
        self.assertIsNone(settings.load_config(self.path))   # nothing left to search: set up again

    def test_internal_fields_can_be_saved(self):
        app = settings.save_app_settings({"background_hint_shown": "2026-10-10T08:00:00Z",
                                          "last_update_check": "2026-10-10T08:00:00Z",
                                          "latest_version": "1.0.1"}, self.path)
        self.assertEqual(app["latest_version"], "1.0.1")

    def test_atomic_write_leaves_no_temp_files(self):
        settings.save_app_settings({"theme": "dark"}, self.path)
        settings.save_config({"keywords": ["Java"], "locations": ["jo"], "sources": ["bayt"]}, self.path)
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["config.json"])
        self.assertEqual(settings.load_app_settings(self.path)["theme"], "dark")


class ValidateSearchSettingsTests(unittest.TestCase):
    ALLOWED = {"jo", "sa", "remote"}

    def test_cleans_and_dedupes(self):
        cleaned = settings.validate_search_settings(
            {"keywords": [" Java ", "Java", "Spring"], "locations": ["JO", "remote"], "sources": ["bayt", "linkedin"]},
            self.ALLOWED)
        self.assertEqual(cleaned, {"keywords": ["Java", "Spring"], "locations": ["jo", "remote"],
                                   "sources": ["bayt", "linkedin"]})

    def test_reports_every_bad_field(self):
        with self.assertRaises(settings.SettingsError) as ctx:
            settings.validate_search_settings({"keywords": [], "locations": ["xx"], "sources": ["myspace"]},
                                              self.ALLOWED)
        self.assertEqual(ctx.exception.errors, {"keywords": "required", "locations": "invalid",
                                                "sources": "invalid"})

    def test_limits_keyword_count_and_length(self):
        with self.assertRaises(settings.SettingsError) as ctx:
            settings.validate_search_settings({"keywords": ["x" * 101], "locations": ["jo"], "sources": ["bayt"]},
                                              self.ALLOWED)
        self.assertEqual(ctx.exception.errors, {"keywords": "too_long"})
        with self.assertRaises(settings.SettingsError) as ctx:
            settings.validate_search_settings({"keywords": [f"k{i}" for i in range(21)], "locations": ["jo"],
                                               "sources": ["bayt"]}, self.ALLOWED)
        self.assertEqual(ctx.exception.errors, {"keywords": "too_many"})

    def test_non_list_fields_are_invalid(self):
        with self.assertRaises(settings.SettingsError) as ctx:
            settings.validate_search_settings({"keywords": "Java", "locations": None}, self.ALLOWED)
        self.assertEqual(ctx.exception.errors, {"keywords": "invalid", "locations": "invalid",
                                                "sources": "invalid"})


class ConcurrentSaveTests(unittest.TestCase):
    def test_concurrent_saves_keep_every_key(self):
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            errors = []

            def app_save(i):
                try:
                    settings.save_app_settings({"latest_version": f"v{i}"} if i % 3 == 0 else
                                               {"theme": ("dark", "light")[i % 2]}, path)
                    settings.save_app_settings({"last_update_check": "2026-01-01T00:00:00Z"}, path)
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

            def search_save():
                try:
                    for _ in range(10):
                        settings.save_config({"keywords": ["Java"], "locations": ["jo"], "sources": ["bayt"]}, path)
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

            threads = [threading.Thread(target=app_save, args=(i,)) for i in range(30)]
            threads += [threading.Thread(target=search_save) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
            self.assertEqual(errors, [])
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["keywords"], ["Java"])
            self.assertEqual(data["app"]["last_update_check"], "2026-01-01T00:00:00Z")
            self.assertIn(data["app"]["latest_version"], {f"v{i}" for i in range(0, 30, 3)})
            self.assertIn(data["app"]["theme"], {"dark", "light"})


if __name__ == "__main__":
    unittest.main()
