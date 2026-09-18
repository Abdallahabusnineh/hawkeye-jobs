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
    - is_relevant_job() from filters.py (user keywords)
    - is_within_48h() from utils.py (date filter)
    - seen_urls set (cross-source URL deduplication within the run)
"""

import time
from datetime import datetime
from bs4 import BeautifulSoup
from filters import is_relevant_job
from utils import is_within_48h


def _parse_bayt_page(html: str, seen_urls: set, location: str, keywords: list) -> list:
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
            if not is_relevant_job(title, keywords):
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


def scrape_bayt(seen_urls: set, driver, searches=None, keywords=None) -> list:
    """Scrape the given Bayt.com searches and return matching job dicts."""
    searches = searches or []
    keywords = keywords or []
    all_jobs = []
    print("\n[Bayt.com]")

    for term_slug, country_slug in searches:
        url = f"https://www.bayt.com/en/{country_slug}/jobs/{term_slug}-jobs/"
        display = f"  '{term_slug}' in {country_slug}"
        try:
            _ensure_window(driver)
            driver.get(url)
            time.sleep(8)
            _ensure_window(driver)

            jobs = _parse_bayt_page(driver.page_source, seen_urls, country_slug, keywords)
            all_jobs.extend(jobs)
            print(f"{display}: {len(jobs)} jobs")
        except Exception as e:
            print(f"{display}: Error - {e}"[:120])

    return all_jobs
