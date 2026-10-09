"""
scraper.py — LinkedIn + Indeed via JobSpy
==========================================
Uses the python-jobspy library (no browser required). Each fetch_* function runs
ONE search and raises on any error; pipeline.py loops over the queries and
decides what to do with failures.

Titles are kept only if they match the user's keywords (is_relevant_job) and
were posted within 48 hours (is_within_48h).
"""

import logging
from datetime import datetime

from jobspy import scrape_jobs

from filters import is_relevant_job
from utils import clean_url, is_within_48h

log = logging.getLogger(__name__)

RESULTS_WANTED = 10
HOURS_OLD = 48


def _parse_jobs(jobs, seen_urls, location, keywords):
    """Convert a JobSpy DataFrame into standardised job dicts."""
    result = []
    skipped = 0
    for _, row in jobs.iterrows():
        url = clean_url(str(row.get("job_url", "")).strip())
        if not url or url in seen_urls:
            continue
        title = str(row.get("title", "")).strip()
        if not is_relevant_job(title, keywords):
            skipped += 1
            continue
        date_p = str(row.get("date_posted", "")).strip()
        if not is_within_48h(date_p):
            skipped += 1
            continue
        seen_urls.add(url)
        company = str(row.get("company", "")).strip()
        loc = str(row.get("location", "")).strip()
        source = str(row.get("site", "")).strip().capitalize()
        result.append({
            "title": title,
            "url": url,
            "description": f"{company} - {loc}" if company else loc,
            "source": source,
            "keyword": "",
            "location": location,
            "published": date_p,
            "date_found": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        })
    if skipped:
        log.debug("Filtered %d irrelevant/old jobs for %s", skipped, location)
    return result


def fetch_linkedin(args: dict, seen_urls: set, keywords: list) -> list:
    """One LinkedIn Jobs search. args = {"term", "location"}."""
    jobs = scrape_jobs(
        site_name=["linkedin"],
        search_term=args["term"],
        location=args["location"],
        results_wanted=RESULTS_WANTED,
        hours_old=HOURS_OLD,
    )
    return _parse_jobs(jobs, seen_urls, args["location"], keywords)


def fetch_indeed(args: dict, seen_urls: set, keywords: list) -> list:
    """One Indeed search. args = {"term", "location", "country"}."""
    jobs = scrape_jobs(
        site_name=["indeed"],
        search_term=args["term"],
        location=args["location"],
        results_wanted=RESULTS_WANTED,
        hours_old=HOURS_OLD,
        country_indeed=args["country"],
    )
    return _parse_jobs(jobs, seen_urls, args["location"], keywords)
