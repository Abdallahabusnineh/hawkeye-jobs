"""
scraper.py — LinkedIn + Indeed scraper via JobSpy
===================================================
Uses the python-jobspy library (no browser required) to search LinkedIn and Indeed.

Responsibilities:
    - Define all LinkedIn and Indeed search queries (term + location).
    - Filter job titles: keep only Flutter / Dart / Mobile / Cross-Platform roles.
    - Filter by date: only jobs posted in the last 48 hours.
    - Deduplicate within a single run using a shared `seen_urls` set.
    - Return a flat list of standardised job dicts.

Title filtering (_is_flutter_job):
    Shared across ALL scrapers — bayt.py, naukrigulf.py, and google_jobs.py all
    import this function so every source applies the same rules.
    Logic:
        1. Reject if any _HARD_EXCLUDE term is found in the lowercased title.
        2. Accept if any _REQUIRED keyword is found ("flutter", "dart", etc.).
        3. Accept if "mobile" appears as a whole word (regex \b boundary prevents
           matching "automobile", "immobile", etc.).
        4. Otherwise reject.

Job dict schema returned by _parse_jobs():
    {
        "title":       str,   # Job title
        "url":         str,   # Direct apply URL (UTM-stripped)
        "description": str,   # "{company} - {location}"
        "source":      str,   # "Linkedin" or "Indeed"
        "keyword":     str,   # Always "" (reserved)
        "location":    str,   # Search location used
        "published":   str,   # Raw date string from the source
        "date_found":  str,   # UTC timestamp of this run
    }
"""

from jobspy import scrape_jobs
from datetime import datetime
import pandas as pd
import re
from utils import is_within_48h, clean_url

LINKEDIN_SEARCHES = [
    # Jordan
    {"term": "Flutter Developer",          "location": "Jordan"},
    {"term": "Dart Developer",             "location": "Jordan"},
    {"term": "Mobile Developer",           "location": "Jordan"},
    # Gulf
    {"term": "Flutter Developer",          "location": "United Arab Emirates"},
    {"term": "Flutter Developer",          "location": "Saudi Arabia"},
    {"term": "Flutter Developer",          "location": "Kuwait"},
    {"term": "Flutter Developer",          "location": "Qatar"},
    {"term": "Flutter Developer",          "location": "Oman"},
    {"term": "Flutter Developer",          "location": "Bahrain"},
    {"term": "Flutter Engineer",           "location": "United Arab Emirates"},
    {"term": "Flutter Engineer",           "location": "Saudi Arabia"},
    {"term": "Mobile Developer Flutter",   "location": "United Arab Emirates"},
    # Europe
    {"term": "Flutter Developer",          "location": "United Kingdom"},
    {"term": "Flutter Developer",          "location": "Germany"},
    {"term": "Flutter Developer",          "location": "Netherlands"},
    {"term": "Flutter Developer",          "location": "France"},
    {"term": "Flutter Engineer",           "location": "United Kingdom"},
    {"term": "Flutter Engineer",           "location": "Germany"},
    {"term": "Cross Platform Developer",   "location": "United Kingdom"},
    # Americas
    {"term": "Flutter Developer",          "location": "United States"},
    {"term": "Flutter Developer",          "location": "Canada"},
    {"term": "Flutter Engineer",           "location": "United States"},
    {"term": "Flutter Engineer",           "location": "Canada"},
    # Remote (Flutter Developer/Engineer Remote causes a JobSpy country-detection bug — skip them)
    {"term": "Cross Platform Developer",   "location": "Remote"},
]

INDEED_SEARCHES = [
    # Gulf
    {"term": "Flutter Developer", "location": "United Arab Emirates", "country": "united arab emirates"},
    {"term": "Flutter Developer", "location": "Saudi Arabia",         "country": "saudi arabia"},
    {"term": "Flutter Developer", "location": "Kuwait",               "country": "kuwait"},
    {"term": "Flutter Developer", "location": "Qatar",                "country": "qatar"},
    {"term": "Flutter Developer", "location": "Oman",                 "country": "oman"},
    {"term": "Flutter Developer", "location": "Bahrain",              "country": "bahrain"},
    {"term": "Flutter Engineer",  "location": "United Arab Emirates", "country": "united arab emirates"},
    {"term": "Flutter Engineer",  "location": "Saudi Arabia",         "country": "saudi arabia"},
    # Europe
    {"term": "Flutter Developer", "location": "United Kingdom",       "country": "uk"},
    {"term": "Flutter Developer", "location": "Germany",              "country": "germany"},
    {"term": "Flutter Developer", "location": "Netherlands",          "country": "netherlands"},
    {"term": "Flutter Engineer",  "location": "United Kingdom",       "country": "uk"},
    # Americas
    {"term": "Flutter Developer", "location": "United States",        "country": "usa"},
    {"term": "Flutter Developer", "location": "Canada",               "country": "canada"},
    {"term": "Flutter Engineer",  "location": "United States",        "country": "usa"},
    {"term": "Mobile Developer",  "location": "United States",        "country": "usa"},
]

GOOGLE_SEARCHES = [
    # Jordan & Gulf
    {"term": "Flutter Developer",        "location": "Jordan"},
    {"term": "Flutter Developer",        "location": "United Arab Emirates"},
    {"term": "Flutter Developer",        "location": "Saudi Arabia"},
    {"term": "Flutter Developer",        "location": "Kuwait"},
    {"term": "Flutter Developer",        "location": "Qatar"},
    {"term": "Flutter Engineer",         "location": "United Arab Emirates"},
    # Europe
    {"term": "Flutter Developer",        "location": "United Kingdom"},
    {"term": "Flutter Developer",        "location": "Germany"},
    {"term": "Flutter Engineer",         "location": "United Kingdom"},
    {"term": "Cross Platform Developer", "location": "United Kingdom"},
    # Americas
    {"term": "Flutter Developer",        "location": "United States"},
    {"term": "Flutter Developer",        "location": "Canada"},
    {"term": "Flutter Engineer",         "location": "United States"},
    # Remote
    {"term": "Flutter Developer Remote", "location": ""},
    {"term": "Flutter Engineer Remote",  "location": ""},
]

# Hard-excluded role patterns — if ANY appear in title → reject
_HARD_EXCLUDE = [
    "qa engineer", "qa lead", "qa manager", "quality assurance", "quality engineer",
    "test engineer", "tester", "automation tester", "automation engineer", "sdet",
    "selenium", "appium", "cypress",
    "ai engineer", "ai developer", "ml engineer", "machine learning",
    "data scientist", "data engineer", "data analyst", "data architect",
    "devops", "site reliability", "sre ",
    "backend developer", "backend engineer",
    "react developer", "react native developer", "angular developer", "vue developer",
    "python developer", "java developer", "node developer", "nodejs",
    "php developer", "ruby developer", ".net developer", "c# developer",
    "ios developer", "android developer",
    "ui/ux", "ux designer", "ui designer", "graphic designer", "product designer",
    "project manager", "product manager", "scrum master", "agile coach",
    "business analyst", "recruiter", "hr ",
    "embedded", "firmware", "hardware",
]

# Keywords — at least one must appear in the title
_REQUIRED = ["flutter", "dart", "cross platform", "cross-platform"]


def _is_flutter_job(title: str) -> bool:
    """
    Return True if the job title is relevant to Flutter / Mobile development.

    Steps:
        1. Lowercase the title.
        2. Reject if any _HARD_EXCLUDE substring is present (e.g. "qa engineer", "ai developer").
        3. Accept if any _REQUIRED keyword is present ("flutter", "dart", "cross platform", "cross-platform").
        4. Accept if the word "mobile" appears as a whole word using regex word boundary (\b),
           which prevents false positives like "automobile" or "immobile".
        5. Reject everything else.

    Imported and used by: bayt.py, naukrigulf.py, google_jobs.py
    """
    t = title.lower()

    # Reject if title clearly indicates a non-Flutter role
    if any(ex in t for ex in _HARD_EXCLUDE):
        return False

    # Accept if title has a direct Flutter/Dart/CrossPlatform keyword
    if any(kw in t for kw in _REQUIRED):
        return True

    # Accept "mobile" as a whole word (not "automobile", "immobile", etc.)
    if re.search(r'\bmobile\b', t):
        return True

    return False


def _parse_jobs(jobs, seen_urls, location):
    """
    Convert a JobSpy DataFrame into a list of standardised job dicts.

    Filters applied:
        - Skips rows with empty or already-seen URLs (using clean_url for canonical form).
        - Calls _is_flutter_job() to reject irrelevant titles.
        - Calls is_within_48h() to reject jobs older than 48 hours.

    Args:
        jobs       : pandas DataFrame returned by scrape_jobs()
        seen_urls  : set of already-processed URLs (mutated in-place)
        location   : the search location string used for this query

    Returns:
        list of job dicts
    """
    result = []
    skipped = 0
    for _, row in jobs.iterrows():
        url = clean_url(str(row.get("job_url", "")).strip())
        if not url or url in seen_urls:
            continue
        title = str(row.get("title", "")).strip()
        if not _is_flutter_job(title):
            skipped += 1
            continue
        date_p = str(row.get("date_posted", "")).strip()
        if not is_within_48h(date_p):
            skipped += 1
            continue
        seen_urls.add(url)
        company = str(row.get("company", "")).strip()
        loc     = str(row.get("location", "")).strip()
        source  = str(row.get("site", "")).strip().capitalize()
        desc    = str(row.get("description", "")).strip()
        desc    = desc[:200] + "..." if len(desc) > 200 else desc
        date_p  = str(row.get("date_posted", "")).strip()
        result.append({
            "title":       title,
            "url":         url,
            "description": f"{company} - {loc}" if company else loc,
            "source":      source,
            "keyword":     "",
            "location":    location,
            "published":   date_p,
            "date_found":  datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        })
    if skipped:
        print(f"    (filtered {skipped} irrelevant)")
    return result


def scrape_all() -> list:
    """
    Run all LinkedIn and Indeed searches and return a combined list of job dicts.

    Iterates LINKEDIN_SEARCHES and INDEED_SEARCHES. For each entry calls JobSpy's
    scrape_jobs() with hours_old=48 and results_wanted=10, then passes the result
    through _parse_jobs() for filtering and normalisation.

    Exceptions per search are caught and printed — one failure does not abort the run.

    Returns:
        list of job dicts (title, url, description, source, keyword, location, published, date_found)
    """
    all_jobs = []
    seen_urls = set()
    print(f"[hawkeye-jobs] Starting search at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    # LinkedIn
    print("\n[LinkedIn]")
    for s in LINKEDIN_SEARCHES:
        try:
            jobs = scrape_jobs(
                site_name      = ["linkedin"],
                search_term    = s["term"],
                location       = s["location"],
                results_wanted = 10,
                hours_old      = 48,
            )
            parsed = _parse_jobs(jobs, seen_urls, s["location"])
            all_jobs.extend(parsed)
            print(f"  '{s['term']}' in {s['location'] or 'Worldwide'}: {len(parsed)} jobs")
        except Exception as e:
            print(f"  '{s['term']}' in {s['location'] or 'Worldwide'}: Error - {e}")

    # Indeed
    print("\n[Indeed]")
    for s in INDEED_SEARCHES:
        try:
            jobs = scrape_jobs(
                site_name      = ["indeed"],
                search_term    = s["term"],
                location       = s["location"],
                results_wanted = 10,
                hours_old      = 48,
                country_indeed = s["country"],
            )
            parsed = _parse_jobs(jobs, seen_urls, s["location"])
            all_jobs.extend(parsed)
            print(f"  '{s['term']}' in {s['location']}: {len(parsed)} jobs")
        except Exception as e:
            print(f"  '{s['term']}' in {s['location']}: Error - {e}")

    print(f"\n[hawkeye-jobs] Total unique jobs from job boards: {len(all_jobs)}")
    return all_jobs
