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

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

TEMP_ROOT = Path(tempfile.mkdtemp(prefix="hawkeye-tests-")).resolve()
atexit.register(shutil.rmtree, TEMP_ROOT, ignore_errors=True)

os.environ["HAWKEYE_DATA_DIR"] = str(TEMP_ROOT / "data")
os.environ["LINKEDIN_PROFILE_DIR"] = str(TEMP_ROOT / "linkedin-profile")
