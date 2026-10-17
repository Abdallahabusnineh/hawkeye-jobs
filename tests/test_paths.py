import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

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
                self.assertEqual(paths.log_path(), target / "hawkeye.log")

    def test_default_uses_platformdirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, _env_without("HAWKEYE_DATA_DIR"), clear=True), \
                 patch("paths.platformdirs.user_data_dir", return_value=tmp) as user_data_dir:
                self.assertEqual(paths.data_dir(), Path(tmp))
            user_data_dir.assert_called_once_with("hawkeye-jobs", appauthor=False)


class OldLinkedInProfileTests(unittest.TestCase):
    def test_old_profile_lives_in_the_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"HAWKEYE_DATA_DIR": tmp}):
                self.assertEqual(paths.old_linkedin_profile_dir(), Path(tmp) / "linkedin-profile")


if __name__ == "__main__":
    unittest.main()
