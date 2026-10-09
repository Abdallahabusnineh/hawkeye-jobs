"""
naukrigulf.py — NaukriGulf scraper via Selenium
=================================================
Scrapes job listings from NaukriGulf.com for Gulf countries.

Why Selenium (not requests)?
    NaukriGulf is an Angular SPA. A plain HTTP request returns the shell HTML with no
    job data — Angular renders the cards via JavaScript. Selenium waits for the JS to run.

URL pattern:
    With location : https://www.naukrigulf.com/{keyword-slug}-jobs-in-{location_slug}
    Without       : https://www.naukrigulf.com/{keyword-slug}-jobs
    Example       : https://www.naukrigulf.com/flutter-developer-jobs-in-uae

HTML selectors (last verified 2026):
    Job card     : .ng-box.srp-tuple
    Link         : a.info-position          (wraps both the URL and title)
    Title text   : .designation-title       (inside the link tag)
    Company      : a.info-org
    Location     : li.info-loc span:last-child
    Date posted  : span.time

    ⚠ NaukriGulf may change its HTML. If jobs return 0, inspect a card and update selectors.

Window management:
    NaukriGulf opens extra browser tabs on some navigations. _ensure_main_window()
    closes all extra tabs and returns focus to the first tab. Called before and after
    every driver.get() to prevent "no such window" Selenium errors.

Filtering:
    - is_relevant_job() from filters.py (user keywords)
    - is_within_48h() from utils.py (date filter)
    - seen_urls set (cross-source URL deduplication within the run)
"""

import logging
from datetime import datetime

from bs4 import BeautifulSoup

from browser import wait_for
from filters import is_relevant_job
from utils import is_within_48h

log = logging.getLogger(__name__)

NAUKRI_WAIT_SECONDS = 15  # Angular SPA render
CARD_SELECTOR = ".ng-box.srp-tuple"

BASE = "https://www.naukrigulf.com"


def _build_url(keyword: str, location_slug: str) -> str:
    """
    Build the NaukriGulf search URL from a keyword and location slug.

    If location_slug is empty or "gulf-region", returns the keyword-only URL
    (searches across the entire Gulf region).
    Otherwise appends -in-{location_slug} for a country-specific search.

    Examples:
        ("flutter developer", "uae")         → .../flutter-developer-jobs-in-uae
        ("flutter engineer",  "gulf-region") → .../flutter-engineer-jobs
        ("Mobile Developer",    "")            → .../dart-developer-jobs
    """
    kw = keyword.replace(" ", "-")
    if location_slug and location_slug not in ("gulf-region", ""):
        return f"{BASE}/{kw}-jobs-in-{location_slug}"
    return f"{BASE}/{kw}-jobs"


def _parse_naukrigulf_page(html: str, seen_urls: set, location: str, keywords: list) -> list:
    """
    Parse a NaukriGulf search results page and return matching job dicts.

    Args:
        html       : raw page HTML from driver.page_source
        seen_urls  : set of already-processed URLs (mutated in-place)
        location   : location slug used for this search (e.g. "uae", "gulf-region")

    Returns:
        list of job dicts with keys: title, url, description, source, keyword,
        location, published, date_found
    """
    soup = BeautifulSoup(html, "html.parser")
    result = []

    for card in soup.select(".ng-box.srp-tuple"):
        try:
            link_tag = card.select_one("a.info-position")
            if not link_tag:
                continue

            title_tag = link_tag.select_one(".designation-title")
            title = title_tag.get_text(strip=True) if title_tag else link_tag.get_text(strip=True)
            if not is_relevant_job(title, keywords):
                continue

            href = link_tag.get("href", "")
            if not href:
                continue
            url = href if href.startswith("http") else f"{BASE}{href}"
            if url in seen_urls:
                continue
            seen_urls.add(url)

            company_tag = card.select_one("a.info-org")
            company = company_tag.get_text(strip=True) if company_tag else ""

            loc_tag = card.select_one("li.info-loc span:last-child")
            loc = loc_tag.get_text(strip=True) if loc_tag else location

            date_tag = card.select_one("span.time")
            date_str = date_tag.get_text(strip=True) if date_tag else ""

            if not is_within_48h(date_str):
                continue

            result.append({
                "title":       title,
                "url":         url,
                "description": f"{company} - {loc}" if company else loc,
                "source":      "NaukriGulf",
                "keyword":     "",
                "location":    location,
                "published":   date_str,
                "date_found":  datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            })
        except Exception:
            continue

    return result


def _ensure_main_window(driver):
    """
    Close any extra browser tabs and switch focus back to the first (main) tab.

    NaukriGulf opens extra tabs when navigating between search pages. If not closed,
    Selenium raises "no such window: target window already closed" on the next
    driver.get() call. Called before and after every navigation.
    """
    try:
        handles = driver.window_handles
        if len(handles) > 1:
            # Close all extra tabs, keep first
            for h in handles[1:]:
                driver.switch_to.window(h)
                driver.close()
        driver.switch_to.window(driver.window_handles[0])
    except Exception:
        pass


def fetch_naukrigulf(driver, args, seen_urls: set, keywords: list) -> list:
    """One NaukriGulf search. args = (keyword, location_slug). Raises on navigation errors."""
    keyword, location_slug = args
    url = _build_url(keyword, location_slug)
    _ensure_main_window(driver)
    driver.get(url)
    loaded = wait_for(driver, CARD_SELECTOR, NAUKRI_WAIT_SECONDS)
    _ensure_main_window(driver)
    jobs = _parse_naukrigulf_page(driver.page_source, seen_urls, location_slug, keywords)
    if not jobs and not loaded:
        log.warning("NaukriGulf: no job cards after %ss at %s (page title: %r)", NAUKRI_WAIT_SECONDS, url, driver.title)
    return jobs
