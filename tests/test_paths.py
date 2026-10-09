import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import paths


def _env_without(*names):
    return {k: v for k, v in os.environ.items() if k not in names}


class DataDirTests(unittest.TestCase):
    def test_env_override_is_used_and_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "data"
            with patch.dict(os.environ, {"HAWKEYE_DATA_DIR": str(target)}):
                self.assertEqual(paths.data_dir(), target)
                self.assertTrue(target.is_dir())
                self.assertEqual(paths.db_path(), target / "hawkeye.db")
                self.assertEqual(paths.config_path(), target / "config.json")
                self.assertEqual(paths.countries_cache_path(), target / "countries_cache.json")
                self.assertEqual(paths.log_path(), target / "hawkeye.log")

    def test_default_uses_platformdirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, _env_without("HAWKEYE_DATA_DIR"), clear=True), \
                 patch("paths.platformdirs.user_data_dir", return_value=tmp) as user_data_dir:
                self.assertEqual(paths.data_dir(), Path(tmp))
            user_data_dir.assert_called_once_with("hawkeye-jobs", appauthor=False)


class LinkedInProfileTests(unittest.TestCase):
    def test_env_var_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            custom = Path(tmp) / "custom-profile"
            with patch.dict(os.environ, {"LINKEDIN_PROFILE_DIR": str(custom), "HAWKEYE_DATA_DIR": tmp}):
                self.assertEqual(paths.linkedin_profile_dir(), custom)

    def test_legacy_profile_used_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "old-profile"
            legacy.mkdir()
            env = _env_without("LINKEDIN_PROFILE_DIR")
            env["HAWKEYE_DATA_DIR"] = str(Path(tmp) / "data")
            with patch.dict(os.environ, env, clear=True), \
                 patch("paths.LEGACY_LINKEDIN_PROFILE", legacy):
                self.assertEqual(paths.linkedin_profile_dir(), legacy)

    def test_falls_back_to_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _env_without("LINKEDIN_PROFILE_DIR")
            env["HAWKEYE_DATA_DIR"] = tmp
            with patch.dict(os.environ, env, clear=True), \
                 patch("paths.LEGACY_LINKEDIN_PROFILE", Path(tmp) / "missing"):
                self.assertEqual(paths.linkedin_profile_dir(), Path(tmp) / "linkedin-profile")


if __name__ == "__main__":
    unittest.main()
