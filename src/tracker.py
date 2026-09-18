"""
tracker.py — Cross-run deduplication
======================================
Prevents the same job from being emailed more than once across separate runs.

How it works:
    Each job is assigned a canonical ID = MD5(clean_url(job["url"])).
    These IDs are stored as a JSON list in seen_jobs.json at the project root.
    Before emailing, filter_new_jobs() loads the file, keeps only jobs whose
    ID is new, then writes the updated set back.

Why MD5 of clean_url (not title or raw URL)?
    - The same job can appear on multiple platforms with slightly different URLs
      (e.g. "...?jk=abc&utm_source=google" vs "...?jk=abc"). clean_url() strips
      the tracking params first so both variants hash to the same ID.
    - Using URL (not title) means a job re-posted with a new title or at a new
      company is correctly treated as a new job.

seen_jobs.json format:
    {
        "seen":  ["md5hash1", "md5hash2", ...],
        "count": 1234
    }

Reset command (re-notify all current listings on next run):
    echo '{"seen": [], "count": 0}' > seen_jobs.json
"""

import json
import hashlib
import os
from utils import clean_url

SEEN_JOBS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "seen_jobs.json"
)


def _load_seen() -> set:
    """Load the set of seen job ID hashes from seen_jobs.json. Returns empty set if file missing."""
    if not os.path.exists(SEEN_JOBS_FILE):
        return set()
    try:
        with open(SEEN_JOBS_FILE, "r") as f:
            data = json.load(f)
            return set(data.get("seen", []))
    except Exception:
        return set()


def _save_seen(seen: set):
    """Persist the updated set of seen job ID hashes to seen_jobs.json."""
    with open(SEEN_JOBS_FILE, "w") as f:
        json.dump({"seen": list(seen), "count": len(seen)}, f, indent=2)


def _job_id(job: dict) -> str:
    """
    Compute the canonical ID for a job.

    ID = MD5( clean_url(url).strip().rstrip("/") )

    Stripping UTM params via clean_url() means the same job URL with different
    tracking parameters (e.g. from Google Jobs vs Indeed directly) produces
    the same hash and is correctly identified as a duplicate.
    """
    url = clean_url(job.get("url", "")).strip().rstrip("/")
    return hashlib.md5(url.encode()).hexdigest()


def filter_new_jobs(jobs: list) -> list:
    """
    Filter out jobs that were already emailed in a previous run.

    Steps:
        1. Load the existing set of seen IDs from seen_jobs.json.
        2. For each job, compute its canonical ID via _job_id().
        3. Keep only jobs whose ID is not in the seen set.
        4. Add the new IDs to the seen set and save back to seen_jobs.json.

    Args:
        jobs : combined list of job dicts from all scrapers

    Returns:
        list of job dicts that have NOT been seen before (safe to email)
    """
    seen = _load_seen()
    new_jobs = []
    new_ids = []

    for job in jobs:
        jid = _job_id(job)
        if jid not in seen:
            new_jobs.append(job)
            new_ids.append(jid)

    if new_ids:
        seen.update(new_ids)
        _save_seen(seen)
        print(f"[tracker] {len(new_jobs)} new jobs found, {len(seen)} total seen")
    else:
        print("[tracker] No new jobs found")

    return new_jobs
