"""
google_jobs.py — Google Jobs scraper via Selenium
===================================================
Scrapes the Google Jobs panel for the user's search terms.

Why Selenium (not JobSpy's Google scraper)?
    JobSpy's built-in Google Jobs scraper returns 0 results in python-jobspy==1.1.80.
    This custom Selenium scraper replaces it entirely.

URL pattern:
    https://www.google.com/search?q={term}+jobs+{location}&ibp=htl;jobs&hl=en
    The `ibp=htl;jobs` parameter activates the Google Jobs panel widget.
    `hl=en` forces English so CSS class names stay consistent across locales.

How extraction works (key insight):
    All 10 job cards AND all their apply links are embedded in the initial page HTML —
    no clicking or interaction is needed. The page is parsed by zipping four parallel
    lists of elements together by array index:

        titles    = soup.select(".PUpOsf")             # [0..9] job title strings
        companies = soup.select(".a3jPc")              # [0..9] company name strings
        locations = soup.select(".FqK3wc")             # [0..9] location strings
        link_grps = soup.select('[role="list"].EDblX') # [0..9] apply-link groups

    Each link_grps[i] contains <a> tags — one per platform hosting that job
    (Indeed, LinkedIn, Glassdoor, etc.). Two link formats are supported:

        1. Absolute https links to the platform (old format). The scraper takes the
           first one that is not on a Google domain (SKIP_DOMAINS).
        2. Google redirect links `href="/goto?url=CAES..."` (current format, relative,
           with `title="Apply on LinkedIn"`). Used only when no absolute link exists;
           the URL becomes `https://www.google.com/goto?url=CAES...`, which redirects
           to the real apply page. The `/goto` token is stable per job across page
           loads, so cross-run dedup on the URL still works.

    When the chosen link's title reads "Apply on X", " (via X)" is appended to the
    description.

    ⚠ Google changes CSS class names periodically. If 0 jobs are returned, open:
      https://www.google.com/search?q=Flutter+Developer+jobs+UAE&ibp=htl;jobs&hl=en
      and inspect the elements to find the new class names.

Date:
    Google Jobs does not expose a date_posted field in its HTML panel.
    `published` is set to "" — jobs are included regardless of age since Google
    already surfaces recent listings.

Filtering:
    - is_relevant_job() from filters.py (user keywords)
    - seen_urls set (cross-source URL deduplication within the run)
    - SKIP_DOMAINS: Google-owned domains are excluded from apply links
"""

import logging
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from browser import wait_for
from filters import is_relevant_job

log = logging.getLogger(__name__)

GOOGLE_WAIT_SECONDS = 15
CARD_SELECTOR = ".PUpOsf"

GOOGLE_ORIGIN = "https://www.google.com"
GOTO_PREFIX = "/goto?url="
APPLY_TITLE_RE = re.compile(r"apply on\s+(.+)", re.IGNORECASE)

SKIP_DOMAINS = ["google.com", "google.jo", "google.ae", "google.co", "accounts.google", "goo.gl"]


def _build_url(term: str, location: str) -> str:
    """
    Build a Google Jobs search URL.

    Args:
        term     : search term, e.g. "Flutter Developer"
        location : location string, e.g. "UAE" or "" for worldwide

    Returns:
        Full Google Jobs URL with ibp=htl;jobs panel activated.
        Example: https://www.google.com/search?q=Flutter+Developer+jobs+UAE&ibp=htl;jobs&hl=en
    """
    q = f"{term} jobs {location}".strip().replace(" ", "+")
    return f"https://www.google.com/search?q={q}&ibp=htl;jobs&hl=en"


def _parse_google_page(html: str, seen_urls: set, location: str, keywords: list) -> list:
    """
    Parse a Google Jobs results page and return matching job dicts.

    Extracts data by zipping four parallel CSS-selected element lists by index:
        .PUpOsf           → job titles
        .a3jPc            → company names
        .FqK3wc           → location strings (may include "• via Indeed" suffix, cleaned)
        [role="list"].EDblX → apply-link groups (one group per job)

    Skips the job if:
        - titles and link_grps have different lengths (page didn't render properly)
        - is_relevant_job() returns False
        - The group has neither an absolute non-Google link nor a /goto?url= link
        - URL is already in seen_urls

    Args:
        html       : raw page HTML from driver.page_source
        seen_urls  : set of already-processed URLs (mutated in-place)
        location   : search location used (e.g. "UAE", "" for worldwide)

    Returns:
        list of job dicts. `published` is always "" (Google doesn't expose posting date).
    """
    soup = BeautifulSoup(html, "html.parser")
    result = []

    titles    = soup.select(".PUpOsf")
    companies = soup.select(".a3jPc")
    locations = soup.select(".FqK3wc")
    link_grps = soup.select('[role="list"].EDblX')

    if not titles or not link_grps or len(titles) != len(link_grps):
        return result

    for i, (t_el, lg) in enumerate(zip(titles, link_grps)):
        try:
            title = t_el.get_text(strip=True)
            if not is_relevant_job(title, keywords):
                continue

            anchors = lg.find_all("a", href=True)
            candidates = [
                a for a in anchors
                if a["href"].startswith("http")
                and not any(skip in a["href"] for skip in SKIP_DOMAINS)
            ] + [a for a in anchors if a["href"].startswith(GOTO_PREFIX)]
            if not candidates:
                continue

            anchor = candidates[0]
            url = anchor["href"]
            if url.startswith(GOTO_PREFIX):
                url = GOOGLE_ORIGIN + url
            if url in seen_urls:
                continue
            seen_urls.add(url)

            platform = APPLY_TITLE_RE.match(anchor.get("title", "").strip())

            company = companies[i].get_text(strip=True) if i < len(companies) else ""
            loc_raw = locations[i].get_text(strip=True) if i < len(locations) else location
            # Clean up "  •  via Indeed" type suffix for location
            loc = loc_raw.split("•")[0].strip() if "•" in loc_raw else loc_raw

            description = f"{company} - {loc}" if company else loc
            if platform:
                description += f" (via {platform.group(1).strip()})"

            result.append({
                "title":       title,
                "url":         url,
                "description": description,
                "source":      "Google Jobs",
                "keyword":     "",
                "location":    location or "Remote/Worldwide",
                "published":   "",
                "date_found":  datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            })
        except Exception:
            continue

    return result


def _close_extra_tabs(driver):
    handles = driver.window_handles
    for handle in handles[1:]:
        driver.switch_to.window(handle)
        driver.close()
    driver.switch_to.window(driver.window_handles[0])


def fetch_google_jobs(driver, args, seen_urls: set, keywords: list) -> list:
    """One Google Jobs search. args = (term, location). Raises on navigation errors."""
    term, location = args
    url = _build_url(term, location)
    driver.get(url)
    loaded = wait_for(driver, CARD_SELECTOR, GOOGLE_WAIT_SECONDS)
    _close_extra_tabs(driver)
    jobs = _parse_google_page(driver.page_source, seen_urls, location, keywords)
    if not jobs and not loaded:
        log.warning("Google Jobs: no job cards after %ss at %s (page title: %r)", GOOGLE_WAIT_SECONDS, url, driver.title)
    return jobs
