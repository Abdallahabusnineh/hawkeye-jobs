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

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from catalog import SOURCES
from countries import CountryFetchError, load_countries
from email_sender import email_configured, send_email
from logs import setup_logging
from filelock import RunLock
from paths import data_dir, linkedin_profile_dir
from pipeline import run_search
from settings import collect_settings, migrate_legacy_config
from store import Store

REPORT_LIMIT = 500  # most jobs reported (printed/emailed) per run

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
    if not run_lock.acquire():
        store.close()
        print("[main] A search is already running in the Hawkeye Jobs app — try again when it finishes.")
        return
    try:
        store.recover_interrupted_runs()
        if store.import_legacy_seen_file(ROOT / "seen_jobs.json"):
            print("Imported your previously seen jobs into the data folder.")
        print(f"Data folder: {data_dir()}")

        chosen = collect_settings()
        try:
            countries = load_countries()
        except CountryFetchError:
            countries = []
        country_labels = {c["id"]: c["label"] for c in countries}

        def print_event(event):
            line = format_event(event, country_labels)
            if line:
                print(line)

        summary = run_search(chosen, on_event=print_event, country_labels=country_labels, store=store,
                             trigger="terminal")

        if summary.state == "failed":
            print("[main] The search stopped because of an error — see hawkeye.log in the data folder.")

        # Report everything not yet reported, including jobs left by an earlier
        # run that was killed before it got this far.
        fetched = store.unnotified_jobs(limit=REPORT_LIMIT + 1)
        pending = fetched[:REPORT_LIMIT]
        if not pending:
            print("[main] No new jobs to report")
            return

        _print_jobs(pending)
        if len(fetched) > REPORT_LIMIT:
            print("[main] More jobs are waiting — they will come in the next report.")
        ids = [job["id"] for job in pending]
        if email_configured():
            print(f"\n[main] Sending email for {len(pending)} new jobs...")
            if send_email(pending):
                store.mark_notified(ids)
            else:
                print("[main] Email failed — these jobs will be included again next time.")
        else:
            # No email set up: the terminal print is the report.
            store.mark_notified(ids)
    finally:
        run_lock.release()
        store.close()

    print("=" * 50)
    print("🦅 hawkeye-jobs done!")
    print("=" * 50)


if __name__ == "__main__":
    main()
