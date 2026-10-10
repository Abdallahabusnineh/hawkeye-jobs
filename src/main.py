#!/usr/bin/env python3
"""
main.py — hawkeye-jobs terminal front-end
==========================================
Prompts for settings (or reuses the saved ones), runs pipeline.run_search(),
prints progress and the new jobs, and emails the new ones.

Ctrl+C stops the search after the queries already running; jobs found so far
are kept, printed and emailed. Runtime files live in the data directory
(paths.data_dir()), not in the repo.
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from catalog import SOURCES
from countries import CountryFetchError, load_countries
from email_sender import email_configured, send_email
from logs import setup_logging
from paths import data_dir, linkedin_profile_dir
from pipeline import run_search
from reporting import REPORT_LIMIT, report_pending
from run_lock import RunLock
from runner import POSTS_SOURCE, apply_posts_gate
from settings import collect_settings, load_app_settings, migrate_legacy_config, save_app_settings
from store import Store

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent

_BROWSER_FAILED = {
    "chrome_not_found": "⚠ Google Chrome is not installed — skipping Bayt, NaukriGulf, Google Jobs and LinkedIn posts.",
    "start_failed": "⚠ Could not start Chrome — skipping browser sources (details in hawkeye.log).",
    "crashed": "⚠ Chrome crashed twice — skipping the remaining browser searches.",
}


def _load_dotenv():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _query_label(event: dict, country_labels: dict) -> str:
    site = SOURCES.get(event["source"], event["source"])
    label = f"  [{site}] '{event['keyword']}'"
    loc_id = event.get("location_id")
    if loc_id:
        label += f" in {country_labels.get(loc_id, loc_id)}"
    return label


def format_event(event: dict, country_labels=None):
    """English terminal line for a pipeline event, or None to print nothing."""
    country_labels = country_labels or {}
    kind = event["type"]
    if kind == "run_started":
        return f"[hawkeye-jobs] Running {event['total']} searches… (Ctrl+C to stop)"
    if kind == "query_done":
        return f"{_query_label(event, country_labels)}: {event['found']} found, {event['new']} new"
    if kind == "query_failed":
        return f"{_query_label(event, country_labels)}: failed ({event['error']}) — details in hawkeye.log"
    if kind == "query_skipped":
        return f"{_query_label(event, country_labels)}: skipped ({event['reason'].replace('_', ' ')})"
    if kind == "login_required":
        return (
            "⚠ Not logged into LinkedIn — skipping post search.\n"
            f'  Log in once: open Chrome with --user-data-dir="{linkedin_profile_dir()}", '
            "sign in to LinkedIn, then close it."
        )
    if kind == "browser_failed":
        return _BROWSER_FAILED.get(event["reason"], f"⚠ Browser problem: {event['reason']}")
    if kind == "run_finished":
        return f"\n[hawkeye-jobs] Search {event['state']}: {event['found']} found, {event['new']} new"
    return None


def _print_jobs(jobs: list):
    print("\n── New jobs ──")
    for i, job in enumerate(jobs, start=1):
        print(f"{i}. {job.get('title', '(no title)')}")
        print(f"   {job.get('description', '')}")
        print(f"   {job.get('source', '')} · {job.get('url', '')}")


def main():
    _load_dotenv()
    setup_logging()
    print("=" * 50)
    print("🦅 hawkeye-jobs")
    print("=" * 50)

    if migrate_legacy_config(ROOT):
        print("Copied your saved search settings into the data folder.")
    store = Store()
    run_lock = RunLock()
    try:
        print(f"Data folder: {data_dir()}")

        chosen = collect_settings()
        # Take the run lock only now, so the app is not blocked while the user sits in the picker.
        if not run_lock.acquire():
            print("[main] Another Hawkeye Jobs search is already running — try again when it finishes.")
            return
        store.recover_interrupted_runs()
        if store.import_legacy_seen_file(ROOT / "seen_jobs.json"):
            print("Imported your previously seen jobs into the data folder.")

        # LinkedIn posts at most once per 24 h, same rule as the app (protects the user's account).
        chosen, posts_skipped = apply_posts_gate(dict(chosen), load_app_settings().get("last_posts_run"),
                                                 None, time.time())
        if posts_skipped:
            print("[main] LinkedIn posts skipped — they already ran within the last 24 hours.")
        posts_marked = False

        try:
            countries = load_countries()
        except CountryFetchError:
            countries = []
        country_labels = {c["id"]: c["label"] for c in countries}

        def print_event(event):
            nonlocal posts_marked
            if event["type"] == "query_started" and event.get("source") == POSTS_SOURCE and not posts_marked:
                posts_marked = True
                try:
                    save_app_settings({"last_posts_run": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
                except Exception:
                    log.exception("Could not save last_posts_run")
            line = format_event(event, country_labels)
            if line:
                print(line)

        summary = run_search(chosen, on_event=print_event, country_labels=country_labels, store=store,
                             trigger="terminal")

        if summary.state == "failed":
            print("[main] The search stopped because of an error — see hawkeye.log in the data folder.")

        def terminal_notifier(jobs) -> bool:
            _print_jobs(jobs)
            if not email_configured():
                return True  # no email set up: the terminal print is the report
            print(f"\n[main] Sending email for {len(jobs)} new jobs...")
            if send_email(jobs):
                return True
            print("[main] Email failed — these jobs will be included again next time.")
            return False

        # Report everything not yet reported, including jobs left by an earlier
        # run that was killed before it got this far.
        result = report_pending(store, terminal_notifier, limit=REPORT_LIMIT)
        if not result.jobs:
            print("[main] No new jobs to report")
            return
        if result.more_waiting:
            print("[main] More jobs are waiting — they will come in the next report.")
    finally:
        run_lock.release()
        store.close()

    print("=" * 50)
    print("🦅 hawkeye-jobs done!")
    print("=" * 50)


if __name__ == "__main__":
    main()
