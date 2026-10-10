"""
utils.py — Shared utility functions
=====================================
Contains two functions used by every scraper:

    clean_url()     — strips UTM and tracking query parameters from a URL so that
                      the same job linked from different platforms hashes to the same ID.

    is_recent()     — returns True if a date string represents a posting within
                      the last 7 days (MAX_AGE_DAYS). Handles multiple real-world date formats
                      found across LinkedIn, Indeed, Bayt, NaukriGulf, and Google Jobs.

Imported by: scraper.py, bayt.py, naukrigulf.py, store.py
"""

from datetime import datetime, timezone
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
import re

MAX_AGE_DAYS = 7  # jobs older than this are filtered out


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


def is_recent(date_str: str, today=None) -> bool:
    """
    Return True if the job was posted within the last MAX_AGE_DAYS (7) days.

    Handles the variety of date formats returned by different job boards:

        Format               Example              Result
        ─────────────────────────────────────────────────────────────
        Empty / None / NaN   "", "none", "nan"    True  (benefit of the doubt)
        Immediate            "just now", "today"  True
        Yesterday            "yesterday"          True
        Hours ago            "3 hours ago"        True if ≤ 7 x 24
        Days ago             "5 days ago"         True if ≤ 7
        Weeks ago            "1 week ago"         True if ≤ 1 ("2 weeks ago" is False)
        Old markers          "30+ days ago"       False (any "+")
                             "1 month ago"        False
                             "1 year ago"         False
        Day-month string     "8 Oct", "8 October" Calendar dates: True if today - date ≤ 7 days
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
    if "+" in s:
        return False                          # "30+ days ago"
    m = re.search(r"(\d+)\s*(?:hour|hr)", s)
    if m:
        return int(m.group(1)) <= MAX_AGE_DAYS * 24
    m = re.search(r"(\d+)\s*day", s)
    if m:
        return int(m.group(1)) <= MAX_AGE_DAYS
    if "week" in s:
        m = re.search(r"(\d+)\s*week", s)
        return (int(m.group(1)) if m else 1) <= 1
    if "month" in s or "year" in s:
        return False

    # "8 Oct" / "8 October" pattern
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            parsed = datetime.strptime(f"{date_str.strip()} {today.year}", fmt).date()
        except ValueError:
            continue
        if parsed > today:
            parsed = parsed.replace(year=today.year - 1)
        return (today - parsed).days <= MAX_AGE_DAYS

    # ISO date "2026-10-08" (optionally with a time part)
    try:
        parsed = datetime.fromisoformat(date_str.strip()).date()
        return (today - parsed).days <= MAX_AGE_DAYS
    except ValueError:
        pass

    return True  # unparseable → include
