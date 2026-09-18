#!/usr/bin/env python3
"""
main.py — hawkeye-jobs orchestrator
=====================================
Entry point for the entire pipeline. Called by run.sh every 2 hours via macOS LaunchAgent.

Pipeline (in order):
    1. scrape_all()         → LinkedIn + Indeed via JobSpy (no browser needed)
    2. create_driver()      → Start one shared Chrome instance (headless=False, required for Cloudflare)
    3. scrape_bayt()        → Bayt.com jobs (Jordan + Gulf)
    4. scrape_naukrigulf()  → NaukriGulf jobs (Gulf)
    5. scrape_google_jobs() → Google Jobs (Gulf, Europe, Americas, Remote)
    6. driver.quit()        → Close Chrome
    7. filter_new_jobs()    → Remove already-emailed jobs using seen_jobs.json
    8. send_email()         → Gmail SMTP SSL HTML digest

Cross-source deduplication within a single run:
    `seen_urls` is built from JobSpy results BEFORE Selenium scrapers run.
    Each Selenium scraper receives and mutates the same set, so if the same
    job URL appears on both Bayt and Google Jobs it is included only once.
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(__file__))

from scraper import scrape_all
from bayt import scrape_bayt
from naukrigulf import scrape_naukrigulf
from google_jobs import scrape_google_jobs
from linkedin_posts import scrape_linkedin_posts
from tracker import filter_new_jobs
from email_sender import send_email


def main():
    print("=" * 50)
    print("🦅 hawkeye-jobs starting...")
    print("=" * 50)

    # ── Stage 1: JobSpy scrapers (no browser) ────────────────────────────────
    # Scrapes LinkedIn and Indeed using the python-jobspy library.
    # Returns a list of job dicts already filtered to Flutter/Mobile titles and ≤48h old.
    all_jobs = scrape_all()

    # Build seen_urls set from JobSpy results.
    # This set is passed to every Selenium scraper so they skip URLs already found.
    seen_urls: set = {job["url"] for job in all_jobs}

    # ── Stage 2: Selenium scrapers (requires Chrome) ─────────────────────────
    # One shared Chrome instance is used for all three Selenium scrapers.
    # headless=False is mandatory — Bayt.com's Cloudflare protection blocks headless Chrome.
    driver = None
    try:
        from browser import create_driver
        # Persistent profile keeps a logged-in LinkedIn session across runs
        # (needed by scrape_linkedin_posts — LinkedIn's content search needs auth).
        # Log into LinkedIn once by hand in this profile; the cookie is reused after.
        profile_dir = os.environ.get("LINKEDIN_PROFILE_DIR", "~/.hawkeye-linkedin-profile")
        print("\n[Browser] Starting Chrome for Bayt, NaukriGulf, Google & LinkedIn Posts...")
        driver = create_driver(headless=False, user_data_dir=profile_dir)
        time.sleep(3)  # wait for Chrome to fully initialize before first navigation

        bayt_jobs = scrape_bayt(seen_urls, driver)        # Bayt.com: Jordan + Gulf
        all_jobs.extend(bayt_jobs)

        naukri_jobs = scrape_naukrigulf(seen_urls, driver) # NaukriGulf: Gulf
        all_jobs.extend(naukri_jobs)

        google_jobs = scrape_google_jobs(seen_urls, driver) # Google Jobs: Gulf, Europe, Americas, Remote
        all_jobs.extend(google_jobs)

        li_posts = scrape_linkedin_posts(seen_urls, driver) # LinkedIn hiring posts (content search)
        all_jobs.extend(li_posts)

    except Exception as e:
        print(f"[Browser] Could not start Chrome: {e}")
        print("[Browser] Skipping Bayt, NaukriGulf, Google & LinkedIn Posts.")
        print("[Browser] If this is a version mismatch, Chrome auto-updated — "
              "the driver now auto-detects the version, but you can force it "
              "with HAWKEYE_CHROME_MAJOR=<n>.")
    finally:
        if driver:
            driver.quit()
            print("[Browser] Chrome closed")

    print(f"\n[hawkeye-jobs] Grand total unique jobs: {len(all_jobs)}")

    if not all_jobs:
        print("[main] No jobs found from any source")
        return

    # ── Stage 3: Cross-run deduplication ─────────────────────────────────────
    # Compares jobs against seen_jobs.json (MD5 hashes of clean URLs).
    # Returns only jobs not seen in any previous run, and updates seen_jobs.json.
    new_jobs = filter_new_jobs(all_jobs)

    # ── Stage 4: Email digest ─────────────────────────────────────────────────
    # Sends a styled HTML email via Gmail SMTP SSL only if there are new jobs.
    if new_jobs:
        print(f"[main] Sending email for {len(new_jobs)} new jobs...")
        send_email(new_jobs)
    else:
        print("[main] No new jobs to report — no email sent")

    print("=" * 50)
    print("🦅 hawkeye-jobs done!")
    print("=" * 50)


if __name__ == "__main__":
    main()
