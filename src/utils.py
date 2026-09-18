"""
utils.py — Shared utility functions
=====================================
Contains two functions used by every scraper:

    clean_url()     — strips UTM and tracking query parameters from a URL so that
                      the same job linked from different platforms hashes to the same ID.

    is_within_48h() — returns True if a date string represents a posting within
                      the last 48 hours. Handles multiple real-world date formats
                      found across LinkedIn, Indeed, Bayt, NaukriGulf, and Google Jobs.

Imported by: scraper.py, bayt.py, naukrigulf.py, tracker.py
"""

from datetime import datetime, timezone
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
import re

MAX_AGE_HOURS = 48  # jobs older than this are filtered out


def clean_url(url: str) -> str:
    """
    Strip UTM and common tracking query parameters from a URL.

    This produces a canonical form so the same job linked from multiple platforms
    (e.g. Google Jobs → Indeed, LinkedIn → Google Jobs) maps to one identical URL
    and therefore one identical MD5 hash in tracker.py.

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


def is_within_48h(date_str: str) -> bool:
    """
    Return True if the job was posted within the last 48 hours (MAX_AGE_HOURS).

    Handles the variety of date formats returned by different job boards:

        Format               Example              Result
        ─────────────────────────────────────────────────────
        Empty / None / NaN   "", "none", "nan"    True  (benefit of the doubt)
        Immediate            "just now", "today"  True
        Hours ago            "3 hours ago"        True if ≤ 48
        Days ago             "1 day ago"          True if ≤ 2
        Yesterday            "yesterday"          True
        Old markers          "30+ days ago"       False
                             "3 weeks ago"        False
                             "1 month ago"        False
        Day-month string     "29 Apr", "1 May"    Compare against today (48h window)
        ISO date             "2026-05-04"         Compare against now (48h window)
        Unparseable          anything else        True  (benefit of the doubt)

    Args:
        date_str : raw date string from the job board (any format above)

    Returns:
        bool — True means include the job, False means skip it
    """
    if not date_str or date_str.lower() in ("none", "nan", "n/a", "-"):
        return True

    s = date_str.lower().strip()

    # Relative patterns
    if any(w in s for w in ("just now", "today", "moments ago")):
        return True
    m = re.search(r"(\d+)\s*hour", s)
    if m:
        return int(m.group(1)) <= MAX_AGE_HOURS
    m = re.search(r"(\d+)\s*day", s)
    if m:
        return int(m.group(1)) <= 2          # 1 day ago or 2 days ago
    if "yesterday" in s:
        return True                           # ~24 h ago
    if "+" in s or "week" in s or "month" in s:
        return False                          # "30+ days ago", "3 weeks ago"

    # "29 Apr" / "1 May" pattern
    for fmt in ("%d %b", "%d %B"):
        try:
            now = datetime.now(timezone.utc)
            parsed = datetime.strptime(date_str.strip(), fmt).replace(
                year=now.year, tzinfo=timezone.utc
            )
            # If parsed date is in the future, it's from last year
            if parsed > now:
                parsed = parsed.replace(year=now.year - 1)
            return (now - parsed).total_seconds() <= MAX_AGE_HOURS * 3600
        except ValueError:
            pass

    # ISO date "2026-05-04"
    try:
        now = datetime.now(timezone.utc)
        parsed = datetime.fromisoformat(date_str.strip()).replace(tzinfo=timezone.utc)
        return (now - parsed).total_seconds() <= MAX_AGE_HOURS * 3600
    except ValueError:
        pass

    return True  # unparseable → include
