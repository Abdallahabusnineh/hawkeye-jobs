"""
paths.py — Where hawkeye-jobs keeps its runtime files
======================================================
Every runtime file (database, config, log) lives in one per-OS data directory,
never in the repo:

    macOS   : ~/Library/Application Support/hawkeye-jobs
    Windows : C:\\Users\\<user>\\AppData\\Local\\hawkeye-jobs
    Linux   : ~/.local/share/hawkeye-jobs   (honours XDG_DATA_HOME)

The Microsoft Store app (an MSIX package) uses its package's own folder instead,
%LOCALAPPDATA%\\Packages\\<family>\\LocalState (see packaged.py).

Set HAWKEYE_DATA_DIR to use another folder. Paths are resolved on every call
(not at import time) so tests and the app can change the env var.
"""

import os
from pathlib import Path

import platformdirs

import packaged

APP_NAME = "hawkeye-jobs"


def data_dir() -> Path:
    """Return the data directory, creating it if needed."""
    override = os.environ.get("HAWKEYE_DATA_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
    elif packaged.package_family_name():
        path = packaged.local_state_dir(packaged.package_family_name())
    else:
        path = Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def unpackaged_data_dir() -> Path:
    """Where the GitHub installer / zip keeps its data. The Store app copies it once (app.py), so
    moving to the Store version keeps the user's jobs and settings."""
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))


def db_path() -> Path:
    return data_dir() / "hawkeye.db"


def config_path() -> Path:
    return data_dir() / "config.json"


def log_path() -> Path:
    return data_dir() / "hawkeye.log"


def old_linkedin_profile_dir() -> Path:
    """The Chrome profile versions up to 1.1.5 kept for the LinkedIn posts login. Unused since;
    app.py deletes it at launch so a logged-in LinkedIn session does not linger on disk."""
    return data_dir() / "linkedin-profile"
