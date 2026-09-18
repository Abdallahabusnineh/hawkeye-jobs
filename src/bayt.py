"""
bayt.py — Bayt.com scraper via Selenium
=========================================
Scrapes job listings from Bayt.com for Jordan and Gulf countries.

Why Selenium (not requests)?
    Bayt.com is protected by Cloudflare. Plain HTTP requests return a 403.
    A visible Chrome window (headless=False) passes the JS challenge after ~8 seconds.

URL pattern:
    https://www.bayt.com/en/{country_slug}/jobs/{term_slug}-jobs/
    Example: https://www.bayt.com/en/uae/jobs/flutter-developer-jobs/

HTML selectors (last verified 2026):
    Job card     : li[data-job-id]
    Title + URL  : .jb-title a, h2 a, h3 a       (tries multiple fallbacks)
    Company      : a.t-default.t-bold
    Location     : .job-company-location-wrapper .t-mute span
    Date posted  : [data-automation-id='job-active-date']

    ⚠ Bayt may change its HTML. If jobs return 0, inspect a job card and update selectors.

Window management:
    Bayt sometimes opens extra browser tabs. _ensure_window() closes them and
    returns focus to the main tab to prevent "no such window" Selenium errors.

Filtering:
    - _is_flutter_job() from scraper.py (shared title filter)
    - is_within_48h() from utils.py (date filter)
    - seen_urls set (cross-source URL deduplication within the run)
"""

import time
from datetime import datetime
from bs4 import BeautifulSoup
from scraper import _is_flutter_job
from utils import is_within_48h

BAYT_SEARCHES = [
    ("flutter-developer", "jordan"),
    ("flutter-developer", "uae"),
    ("flutter-developer", "saudi-arabia"),
    ("flutter-developer", "kuwait"),
    ("flutter-developer", "qatar"),
    ("flutter-developer", "oman"),
    ("flutter-developer", "bahrain"),
    ("flutter-engineer",  "uae"),
    ("flutter-engineer",  "saudi-arabia"),
    ("mobile-developer",  "jordan"),
    ("mobile-developer",  "uae"),
    ("mobile-developer",  "saudi-arabia"),
    ("dart-developer",    "jordan"),
    ("dart-developer",    "uae"),
]


def _parse_bayt_page(html: str, seen_urls: set, location: str) -> list:
    """
    Parse a Bayt.com search results page and return matching job dicts.

    Args:
        html       : raw page HTML from driver.page_source
        seen_urls  : set of already-processed URLs (mutated in-place to add new ones)
        location   : country slug used in the search (e.g. "uae", "jordan")

    Returns:
        list of job dicts with keys: title, url, description, source, keyword,
        location, published, date_found
    """
    soup = BeautifulSoup(html, "html.parser")
    result = []

    for li in soup.select("li[data-job-id]"):
        try:
            title_tag = li.select_one(".jb-title a, h2 a, h3 a")
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            if not _is_flutter_job(title):
                continue

            href = title_tag.get("href", "")
            if not href:
                continue
            url = href if href.startswith("http") else f"https://www.bayt.com{href}"
            if url in seen_urls:
                continue
            seen_urls.add(url)

            company_tag = li.select_one("a.t-default.t-bold")
            company = company_tag.get_text(strip=True) if company_tag else ""

            loc_tag = li.select_one(".job-company-location-wrapper .t-mute span")
            loc = loc_tag.get_text(strip=True) if loc_tag else location

            date_tag = li.select_one("[data-automation-id='job-active-date']")
            date_str = date_tag.get_text(strip=True) if date_tag else ""

            if not is_within_48h(date_str):
                continue

            result.append({
                "title":       title,
                "url":         url,
                "description": f"{company} - {loc}" if company else loc,
                "source":      "Bayt",
                "keyword":     "",
                "location":    location,
                "published":   date_str,
                "date_found":  datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            })
        except Exception:
            continue

    return result


def _ensure_window(driver):
    """
    Close any extra browser tabs and switch focus back to the first (main) tab.

    Bayt.com occasionally opens extra tabs during navigation. If left open they
    cause Selenium to raise "no such window: target window already closed" on
    the next driver.get() call. This helper is called before and after every
    navigation to keep the driver on a single clean tab.
    """
    try:
        handles = driver.window_handles
        for h in handles[1:]:
            driver.switch_to.window(h)
            driver.close()
        driver.switch_to.window(driver.window_handles[0])
    except Exception:
        pass


def scrape_bayt(seen_urls: set, driver) -> list:
    """
    Scrape all BAYT_SEARCHES and return a combined list of job dicts.

    For each search:
        1. Calls _ensure_window() to reset the tab state.
        2. Navigates to the Bayt.com search URL.
        3. Waits 8 seconds for Cloudflare JS challenge to resolve and page to render.
        4. Calls _ensure_window() again in case new tabs were opened.
        5. Passes driver.page_source to _parse_bayt_page() for extraction.

    Args:
        seen_urls : set of URLs already found in this run (shared with other scrapers)
        driver    : Selenium WebDriver instance from browser.create_driver()

    Returns:
        list of job dicts
    """
    all_jobs = []
    print("\n[Bayt.com]")

    for term_slug, country_slug in BAYT_SEARCHES:
        url = f"https://www.bayt.com/en/{country_slug}/jobs/{term_slug}-jobs/"
        display = f"  '{term_slug}' in {country_slug}"
        try:
            _ensure_window(driver)
            driver.get(url)
            time.sleep(8)  # wait for Cloudflare challenge + JS render
            _ensure_window(driver)

            jobs = _parse_bayt_page(driver.page_source, seen_urls, country_slug)
            all_jobs.extend(jobs)
            print(f"{display}: {len(jobs)} jobs")
        except Exception as e:
            print(f"{display}: Error - {e}"[:120])

    return all_jobs
