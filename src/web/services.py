"""
services.py — Everything the local API needs from the outside world
====================================================================
The API handlers only talk to an AppServices object, so tests can swap in
fakes (no network, no Chrome, no real folders) and app.py wires the real ones.
"""

import getpass
import json
import logging
import os
import platform
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

RELEASES_API = "https://api.github.com/repos/Abdallahabusnineh/hawkeye-jobs/releases/latest"


def _open_path(path) -> None:
    path = str(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform == "win32":
        os.startfile(path)  # noqa: S606 — opening the user's own data folder
    else:
        subprocess.Popen(["xdg-open", path])


def _launch(argv) -> None:
    subprocess.Popen(list(argv), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _fetch_latest_release() -> dict:
    import requests
    response = requests.get(RELEASES_API, timeout=5,
                            headers={"Accept": "application/vnd.github+json", "User-Agent": "hawkeye-jobs"})
    response.raise_for_status()
    return response.json()


def _base_countries_factory():
    cache = {}
    lock = threading.Lock()

    def base_countries():
        if "rows" not in cache:
            with lock:
                if "rows" not in cache:
                    from countries import CountryFetchError, load_countries, offline_countries
                    try:
                        cache["rows"] = load_countries()
                    except CountryFetchError:
                        cache["rows"] = offline_countries()
        return cache["rows"]
    return base_countries


@dataclass
class AppServices:
    manager: object
    store_factory: Callable
    load_config: Callable
    save_config: Callable
    load_app_settings: Callable
    save_app_settings: Callable
    base_countries: Callable
    find_chrome: Callable
    linkedin_profile_dir: Callable
    data_dir: Callable
    log_path: Callable
    open_url: Callable
    open_path: Callable
    launch: Callable
    fetch_latest_release: Callable
    version: str
    clock: Callable = time.time
    os_name: str = field(default_factory=lambda: f"{platform.system()} {platform.release()}")
    home: str = field(default_factory=lambda: str(Path.home()))
    user: str = field(default_factory=getpass.getuser)

    @classmethod
    def default(cls, manager) -> "AppServices":
        import paths
        import settings
        from browser import find_chrome
        from store import Store
        from version import __version__
        return cls(
            manager=manager,
            store_factory=Store,
            load_config=settings.load_config,
            save_config=settings.save_config,
            load_app_settings=settings.load_app_settings,
            save_app_settings=settings.save_app_settings,
            base_countries=_base_countries_factory(),
            find_chrome=find_chrome,
            linkedin_profile_dir=paths.linkedin_profile_dir,
            data_dir=paths.data_dir,
            log_path=paths.log_path,
            open_url=webbrowser.open,
            open_path=_open_path,
            launch=_launch,
            fetch_latest_release=_fetch_latest_release,
            version=__version__,
        )
