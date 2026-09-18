"""
browser.py — Shared Chrome driver factory
==========================================
Creates a single undetected-chromedriver (Selenium) instance that is shared
across all Selenium-based scrapers: bayt.py, naukrigulf.py, google_jobs.py.

Why undetected-chromedriver?
    Regular Selenium is detected and blocked by Cloudflare (used on Bayt.com).
    undetected-chromedriver patches the Chrome binary at runtime to remove
    automation fingerprints.

Why headless=False?
    Bayt.com's Cloudflare protection specifically blocks headless Chrome.
    Running with a visible window passes the bot detection challenge.

version_main:
    Must match the installed Chrome version on this machine.
    Check with: google-chrome --version  (or Chrome → About Google Chrome)
    Update `version_main=149` in create_driver() if Chrome is updated.
"""

import os
import re
import shutil
import subprocess
import undetected_chromedriver as uc


def _detect_chrome_major():
    """
    Detect the installed Chrome major version so ChromeDriver matches it.

    Chrome auto-updates itself but ChromeDriver does not, so a hardcoded pin
    breaks (session not created) every time Chrome jumps a major version.
    Returning None lets undetected-chromedriver auto-resolve the version.

    Override with env var HAWKEYE_CHROME_MAJOR if detection is wrong.
    """
    override = os.environ.get("HAWKEYE_CHROME_MAJOR")
    if override:
        try:
            return int(override)
        except ValueError:
            pass

    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",  # macOS
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chrome"),
    ]
    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        try:
            out = subprocess.check_output([path, "--version"], text=True, timeout=10)
        except (subprocess.SubprocessError, OSError):
            continue
        m = re.search(r"(\d+)\.\d+\.\d+", out)
        if m:
            return int(m.group(1))
    return None


def create_driver(headless=False, user_data_dir=None):
    """
    Create and return a configured undetected-chromedriver instance.

    Args:
        headless (bool): Run Chrome with no visible window.
                         Default False — Bayt.com blocks headless mode via Cloudflare.
        user_data_dir (str|None): Path to a persistent Chrome profile directory.
                         When set, Chrome reuses this profile across runs so a
                         logged-in session (e.g. LinkedIn) survives between runs.
                         Needed by linkedin_posts.py — LinkedIn's content search
                         requires an authenticated session. Log in once manually
                         in this profile; the cookie is reused every run after.

    Chrome options explained:
        --no-sandbox                       : required in some Linux/CI environments
        --disable-dev-shm-usage            : prevents crash in low-memory environments
        --window-size=1920,1080            : ensures a full-size viewport for JS rendering
        --lang=en-US                       : forces English UI so selectors stay consistent
        --disable-blink-features=AutomationControlled : removes the "Chrome is controlled by
                                            automated software" flag from the browser

    version_main:
        The installed Chrome major version, auto-detected at runtime by
        _detect_chrome_major(). Chrome auto-updates but ChromeDriver does not,
        so a hardcoded pin breaks ("session not created") on every major bump.
        Override detection with env var HAWKEYE_CHROME_MAJOR if needed.

    page_load_timeout=30:
        Raises TimeoutException if a page takes longer than 30 seconds to load.

    Returns:
        uc.Chrome instance (Selenium WebDriver)
    """
    opts = uc.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--lang=en-US")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    if user_data_dir:
        user_data_dir = os.path.expanduser(user_data_dir)
        os.makedirs(user_data_dir, exist_ok=True)
        opts.add_argument(f"--user-data-dir={user_data_dir}")

    driver = uc.Chrome(options=opts, version_main=_detect_chrome_major())
    driver.set_page_load_timeout(30)
    return driver
