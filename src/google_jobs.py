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
    (Indeed, LinkedIn, Glassdoor, etc.). The scraper takes the first non-Google URL.

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

import time
from datetime import datetime
from bs4 import BeautifulSoup
from filters import is_relevant_job

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
        - All apply links in the group are Google-owned domains (SKIP_DOMAINS)
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

            urls = [
                a["href"] for a in lg.find_all("a", href=True)
                if a["href"].startswith("http")
                and not any(skip in a["href"] for skip in SKIP_DOMAINS)
            ]
            if not urls:
                continue

            url = urls[0]
            if url in seen_urls:
                continue
            seen_urls.add(url)

            company = companies[i].get_text(strip=True) if i < len(companies) else ""
            loc_raw = locations[i].get_text(strip=True) if i < len(locations) else location
            # Clean up "  •  via Indeed" type suffix for location
            loc = loc_raw.split("•")[0].strip() if "•" in loc_raw else loc_raw

            result.append({
                "title":       title,
                "url":         url,
                "description": f"{company} - {loc}" if company else loc,
                "source":      "Google Jobs",
                "keyword":     "",
                "location":    location or "Remote/Worldwide",
                "published":   "",
                "date_found":  datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            })
        except Exception:
            continue

    return result


def scrape_google_jobs(seen_urls: set, driver, searches=None, keywords=None) -> list:
    """Scrape the given Google Jobs searches and return matching job dicts."""
    searches = searches or []
    keywords = keywords or []
    all_jobs = []
    print("\n[Google Jobs]")

    for term, location in searches:
        url = _build_url(term, location)
        display = f"  '{term}' in {location or 'Worldwide'}"
        try:
            driver.get(url)
            time.sleep(7)

            handles = driver.window_handles
            for h in handles[1:]:
                driver.switch_to.window(h)
                driver.close()
            driver.switch_to.window(driver.window_handles[0])

            jobs = _parse_google_page(driver.page_source, seen_urls, location, keywords)
            all_jobs.extend(jobs)
            print(f"{display}: {len(jobs)} jobs")
        except Exception as e:
            print(f"{display}: Error - {e}"[:120])

    return all_jobs
