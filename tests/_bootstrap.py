"""_bootstrap.py — point all runtime data at a throwaway folder during tests.
====================================================================

Every test file imports this before any src module. It adds src/ to
sys.path and sets HAWKEYE_DATA_DIR / LINKEDIN_PROFILE_DIR to a temp folder
that is deleted at exit, so a test can never read or write the user's real
config.json, database, logs or LinkedIn profile. tests/test_isolation.py
fails if a test file forgets to import it.
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import platformdirs

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

TEMP_ROOT = Path(tempfile.mkdtemp(prefix="hawkeye-tests-")).resolve()
atexit.register(shutil.rmtree, TEMP_ROOT, ignore_errors=True)

os.environ["HAWKEYE_DATA_DIR"] = str(TEMP_ROOT / "data")
os.environ["LINKEDIN_PROFILE_DIR"] = str(TEMP_ROOT / "linkedin-profile")

# Fallback for a test that clears os.environ: platformdirs must not reach the
# real user folder either.
REAL_USER_DATA_DIR = platformdirs.user_data_dir  # kept only for isolation assertions


def _tmp_user_data_dir(appname=None, *args, **kwargs):
    return str(TEMP_ROOT / "platformdirs-data" / (appname or "app"))


platformdirs.user_data_dir = _tmp_user_data_dir

# paths.linkedin_profile_dir() falls back to a legacy home-folder profile when
# it exists; point that at the temp dir too so a cleared environ can't see it.
import paths  # noqa: E402

paths.LEGACY_LINKEDIN_PROFILE = TEMP_ROOT / "legacy-linkedin-profile"
