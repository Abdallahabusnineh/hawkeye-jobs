"""
browser.py — Shared Chrome driver factory
==========================================
Creates the undetected-chromedriver (Selenium) instance shared by the browser
scrapers: bayt.py, naukrigulf.py, google_jobs.py, linkedin_posts.py.

Why undetected-chromedriver?
    Regular Selenium is detected and blocked by Cloudflare (used on Bayt.com).
    undetected-chromedriver patches the Chrome binary at runtime to remove
    automation fingerprints.

Why headless=False?
    Bayt.com's Cloudflare protection specifically blocks headless Chrome.
    Running with a visible window passes the bot detection challenge.

Chrome discovery and version:
    find_chrome() looks in the standard macOS / Windows install folders, then on
    PATH. The Chrome major version is detected at runtime (Windows: registry,
    elsewhere: `chrome --version`) so ChromeDriver always matches; there is no
    hardcoded version. Override with HAWKEYE_CHROME_MAJOR if detection is wrong.
    create_driver() raises ChromeNotFound when Chrome is not installed.

wait_for():
    Scrapers wait until a job-card selector appears instead of sleeping a fixed
    number of seconds.
"""

import os
import re
import shutil
import subprocess
import sys

import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
_PATH_NAMES = ("google-chrome", "google-chrome-stable", "chrome")


class ChromeNotFound(RuntimeError):
    """Google Chrome is not installed (or not in any place we look)."""


def _windows_candidates() -> list:
    paths = []
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            paths.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
    return paths


def find_chrome():
    """Return the path to the installed Google Chrome binary, or None."""
    candidates = [MAC_CHROME, *_windows_candidates()]
    candidates += [shutil.which(name) for name in _PATH_NAMES]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def _windows_chrome_major():
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"Software\Google\Chrome\BLBeacon") as key:
                version, _ = winreg.QueryValueEx(key, "version")
        except OSError:
            continue
        match = re.match(r"(\d+)\.", str(version))
        if match:
            return int(match.group(1))
    return None


def _detect_chrome_major(chrome_path=None):
    """
    Installed Chrome major version, so ChromeDriver matches it. None lets
    undetected-chromedriver resolve the version itself.
    """
    override = os.environ.get("HAWKEYE_CHROME_MAJOR")
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    if sys.platform == "win32":
        return _windows_chrome_major()  # chrome.exe --version prints nothing on Windows
    path = chrome_path or find_chrome()
    if not path:
        return None
    try:
        out = subprocess.check_output([path, "--version"], text=True, timeout=10)
    except (subprocess.SubprocessError, OSError):
        return None
    match = re.search(r"(\d+)\.\d+\.\d+", out)
    return int(match.group(1)) if match else None


def wait_for(driver, css: str, timeout: float) -> bool:
    """Wait until an element matching `css` exists. False on timeout — the caller parses whatever loaded."""
    try:
        WebDriverWait(driver, timeout, poll_frequency=0.25).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, css))
        )
        return True
    except TimeoutException:
        return False


def create_driver(headless=False, user_data_dir=None):
    """
    Create and return a configured undetected-chromedriver instance.

    Args:
        headless (bool): Run Chrome with no visible window.
                         Default False — Bayt.com blocks headless mode via Cloudflare.
        user_data_dir (str|None): Persistent Chrome profile directory, so a
                         logged-in LinkedIn session survives between runs
                         (see paths.linkedin_profile_dir()).

    Chrome options explained:
        --no-sandbox                       : required in some Linux/CI environments
        --disable-dev-shm-usage            : prevents crash in low-memory environments
        --window-size=1920,1080            : ensures a full-size viewport for JS rendering
        --lang=en-US                       : forces English UI so selectors stay consistent
        --disable-blink-features=AutomationControlled : removes the "Chrome is controlled by
                                            automated software" flag from the browser

    Raises:
        ChromeNotFound: Google Chrome is not installed.
    """
    chrome = find_chrome()
    if not chrome:
        raise ChromeNotFound("Google Chrome is not installed")

    opts = uc.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--lang=en-US")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    if user_data_dir:
        user_data_dir = os.path.expanduser(str(user_data_dir))
        os.makedirs(user_data_dir, exist_ok=True)
        opts.add_argument(f"--user-data-dir={user_data_dir}")

    driver = uc.Chrome(
        options=opts,
        browser_executable_path=chrome,
        version_main=_detect_chrome_major(chrome),
    )
    driver.set_page_load_timeout(30)
    return driver
