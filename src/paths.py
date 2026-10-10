"""
paths.py — Where hawkeye-jobs keeps its runtime files
======================================================
Every runtime file (database, config, country cache, log, LinkedIn Chrome
profile) lives in one per-OS data directory, never in the repo:

    macOS   : ~/Library/Application Support/hawkeye-jobs
    Windows : C:\\Users\\<user>\\AppData\\Local\\hawkeye-jobs
    Linux   : ~/.local/share/hawkeye-jobs   (honours XDG_DATA_HOME)

Set HAWKEYE_DATA_DIR to use another folder. Paths are resolved on every call
(not at import time) so tests and the app can change the env var.
"""

import os
from pathlib import Path

import platformdirs

APP_NAME = "hawkeye-jobs"
LEGACY_LINKEDIN_PROFILE = Path("~/.hawkeye-linkedin-profile")


def data_dir() -> Path:
    """Return the data directory, creating it if needed."""
    override = os.environ.get("HAWKEYE_DATA_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
    else:
        path = Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "hawkeye.db"


def config_path() -> Path:
    return data_dir() / "config.json"


def log_path() -> Path:
    return data_dir() / "hawkeye.log"


def linkedin_profile_dir() -> Path:
    """
    Chrome profile used for the LinkedIn login.

    LINKEDIN_PROFILE_DIR wins; otherwise the old ~/.hawkeye-linkedin-profile is
    kept if it exists (so existing users stay logged in); otherwise
    <data>/linkedin-profile.
    """
    override = os.environ.get("LINKEDIN_PROFILE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    legacy = LEGACY_LINKEDIN_PROFILE.expanduser()
    if legacy.exists():
        return legacy
    return data_dir() / "linkedin-profile"
