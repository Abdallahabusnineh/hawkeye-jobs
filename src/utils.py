"""
utils.py — Shared utility functions
=====================================
Contains two functions used by every scraper:

    clean_url()     — strips UTM and tracking query parameters from a URL so that
                      the same job linked from different platforms hashes to the same ID.

    is_recent()     — returns True if a date string represents a posting within
                      the user's job-age window (7/14/30 days). Handles multiple real-world date formats
                      found across LinkedIn, Indeed, Bayt, NaukriGulf, and Google Jobs.

Imported by: scraper.py, bayt.py, naukrigulf.py, store.py
"""

from datetime import datetime, timezone
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
import re

# How old a job may be (days since posted). The user picks it in the window (Search → job age);
# run_search() sets it for the run. One search runs at a time per process (run_lock), so a
# module-level value is enough and saves threading it through every site's fetcher.
JOB_AGE_CHOICES = (7, 14, 30)
DEFAULT_JOB_AGE_DAYS = 14
_job_age_days = DEFAULT_JOB_AGE_DAYS


def set_job_age_days(days) -> int:
    """Use `days` for the coming search (one of JOB_AGE_CHOICES; anything else → the default)."""
    global _job_age_days
    _job_age_days = days if days in JOB_AGE_CHOICES else DEFAULT_JOB_AGE_DAYS
    return _job_age_days


def job_age_days() -> int:
    return _job_age_days


def clean_url(url: str) -> str:
    """
    Strip UTM and common tracking query parameters from a URL.

    This produces a canonical form so the same job linked from multiple platforms
    (e.g. Google Jobs → Indeed, LinkedIn → Google Jobs) maps to one identical URL
    and therefore one identical MD5 hash in store.py.

    Parameters stripped:
        utm_source, utm_medium, utm_campaign, utm_content, utm_term,
        source, ref, referer, tracking

    Example:
        Input : https://indeed.com/viewjob?jk=abc123&utm_source=google&utm_campaign=x
        Output: https://indeed.com/viewjob?jk=abc123

    Args:
        url : raw URL string

    Returns:
        URL with tracking params removed. Returns the original URL if parsing fails.
    """
    try:
        p = urlparse(url)
        params = parse_qs(p.query, keep_blank_values=False)
        STRIP = {"utm_source", "utm_medium", "utm_campaign", "utm_content",
                 "utm_term", "source", "ref", "referer", "tracking"}
        kept = {k: v for k, v in params.items() if k.lower() not in STRIP}
        return urlunparse(p._replace(query=urlencode(kept, doseq=True)))
    except Exception:
        return url


def is_recent(date_str: str, today=None, max_days=None) -> bool:
    """
    Return True if the job was posted within the last `max_days` days (default: job_age_days(),
    the user's setting — 7, 14 or 30).

    Handles the variety of date formats returned by different job boards:

        Format               Example              Result
        ─────────────────────────────────────────────────────────────
        Empty / None / NaN   "", "none", "nan"    True  (benefit of the doubt)
        Immediate            "just now", "today"  True
        Yesterday            "yesterday"          True
        (N = max_days: 7, 14 or 30)
        Hours ago            "3 hours ago"        True if ≤ N x 24
        Days ago             "5 days ago"         True if ≤ N
        Weeks ago            "2 weeks ago"        True if weeks x 7 ≤ N
        Months ago           "1 month ago"        True if months x 30 ≤ N (only the 30-day window)
        Old markers          "30+ days ago"       False (any "+")
                             "1 year ago"         False
        Day-month string     "8 Oct", "8 October" Calendar dates: True if today - date ≤ N days
        ISO date / datetime  "2026-10-08"         Calendar dates (a time part is ignored)
        Unparseable          anything else        True  (benefit of the doubt)

    Dates are compared as calendar dates in UTC, so "2026-10-08" and
    "2 days ago" agree. A day-month date in the future is from last year.

    Args:
        date_str : raw date string from the job board (any format above)
        today    : the current UTC date (a date or datetime); defaults to now. For tests.

    Returns:
        bool — True means include the job, False means skip it
    """
    if not date_str or str(date_str).lower() in ("none", "nan", "n/a", "-"):
        return True
    max_days = max_days or job_age_days()

    s = str(date_str).lower().strip()

    if today is None:
        today = datetime.now(timezone.utc).date()
    elif isinstance(today, datetime):
        today = today.date()

    # Relative patterns
    if any(w in s for w in ("just now", "today", "moments ago")):
        return True
    if "yesterday" in s:
        return True
    if re.search(r"\d\+(?!\d)", s):
        return False                          # "30+ days ago" (not an ISO "+03:00" offset)
    m = re.search(r"(\d+)\s*(?:hour|hr)", s)
    if m:
        return int(m.group(1)) <= max_days * 24
    m = re.search(r"(\d+)\s*day", s)
    if m:
        return int(m.group(1)) <= max_days
    if "week" in s:
        m = re.search(r"(\d+)\s*week", s)
        return (int(m.group(1)) if m else 1) * 7 <= max_days
    if "month" in s:
        m = re.search(r"(\d+)\s*month", s)
        return (int(m.group(1)) if m else 1) * 30 <= max_days
    if "year" in s:
        return False

    # "8 Oct" / "8 October" pattern
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            parsed = datetime.strptime(f"{date_str.strip()} {today.year}", fmt).date()
        except ValueError:
            continue
        if (parsed - today).days > 1:         # 1 day of slack: local date can be ahead of UTC
            try:
                parsed = parsed.replace(year=today.year - 1)
            except ValueError:                # 29 Feb rolled back into a non-leap year
                return False
        return (today - parsed).days <= max_days

    # ISO date "2026-10-08" (optionally with a time part)
    try:
        parsed = datetime.fromisoformat(date_str.strip()).date()
        return (today - parsed).days <= max_days
    except ValueError:
        pass

    return True  # unparseable → include
