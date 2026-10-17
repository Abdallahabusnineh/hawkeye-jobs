import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

import paths

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent


def _inside(path, parent):
    return Path(path).resolve().is_relative_to(Path(parent).resolve())


class IsolationTests(unittest.TestCase):
    def test_every_test_file_imports_bootstrap(self):
        missing = [
            p.name
            for p in sorted(TESTS_DIR.glob("test_*.py"))
            if not any(
                line.startswith("import _bootstrap")
                for line in p.read_text(encoding="utf-8").splitlines()
            )
        ]
        self.assertEqual(missing, [], f"test files missing 'import _bootstrap': {missing}")

    def test_data_dir_is_in_bootstrap_temp_dir(self):
        data = paths.data_dir()
        self.assertTrue(_inside(data, _bootstrap.TEMP_ROOT), data)
        self.assertFalse(_inside(data, _bootstrap.REAL_USER_DATA_DIR("hawkeye-jobs", appauthor=False)))
        self.assertFalse(_inside(data, REPO_ROOT))

    def test_data_dir_with_cleared_environ_stays_in_temp_dir(self):
        with patch.dict(os.environ, {}, clear=True):
            data = paths.data_dir()
        self.assertTrue(_inside(data, _bootstrap.TEMP_ROOT), data)

    def test_old_linkedin_profile_dir_with_cleared_environ_stays_in_temp_dir(self):
        with patch.dict(os.environ, {}, clear=True):
            profile = paths.old_linkedin_profile_dir()
        self.assertTrue(_inside(profile, _bootstrap.TEMP_ROOT), profile)


if __name__ == "__main__":
    unittest.main()
