#!/usr/bin/env python3
"""
main.py — hawkeye-jobs orchestrator
=====================================
Interactive, one-shot pipeline. The user picks keywords, locations, and sources
in the terminal. Choices are saved to config.json for the next run.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from catalog import BROWSER_SOURCES, expand_searches
from scraper import scrape_all
from bayt import scrape_bayt
from naukrigulf import scrape_naukrigulf
from google_jobs import scrape_google_jobs
from linkedin_posts import scrape_linkedin_posts
from tracker import filter_new_jobs
from email_sender import send_email
from settings import collect_settings


ROOT = Path(__file__).resolve().parent.parent


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


def _print_jobs(jobs: list):
    print("\n── New jobs ──")
    for i, job in enumerate(jobs, start=1):
        print(f"{i}. {job.get('title', '(no title)')}")
        print(f"   {job.get('description', '')}")
        print(f"   {job.get('source', '')} · {job.get('url', '')}")


def main():
    _load_dotenv()
    print("=" * 50)
    print("🦅 hawkeye-jobs")
    print("=" * 50)

    chosen = collect_settings()
    searches = expand_searches(
        chosen["keywords"],
        chosen["locations"],
        chosen["sources"],
    )
    keywords = chosen["keywords"]

    all_jobs = scrape_all(
        linkedin_searches=searches["linkedin"],
        indeed_searches=searches["indeed"],
        keywords=keywords,
    )
    seen_urls: set = {job["url"] for job in all_jobs}

    need_browser = any(searches[key] for key in BROWSER_SOURCES)
    driver = None
    if need_browser:
        try:
            from browser import create_driver
            profile_dir = os.environ.get("LINKEDIN_PROFILE_DIR", "~/.hawkeye-linkedin-profile")
            print("\n[Browser] Starting Chrome...")
            driver = create_driver(headless=False, user_data_dir=profile_dir)
            time.sleep(3)

            if searches["bayt"]:
                all_jobs.extend(scrape_bayt(seen_urls, driver, searches["bayt"], keywords))
            if searches["naukrigulf"]:
                all_jobs.extend(scrape_naukrigulf(seen_urls, driver, searches["naukrigulf"], keywords))
            if searches["google_jobs"]:
                all_jobs.extend(scrape_google_jobs(seen_urls, driver, searches["google_jobs"], keywords))
            if searches["linkedin_posts"]:
                all_jobs.extend(
                    scrape_linkedin_posts(seen_urls, driver, searches["linkedin_posts"], keywords)
                )
        except Exception as e:
            print(f"[Browser] Could not start Chrome: {e}")
            print("[Browser] Skipping browser-based sources.")
        finally:
            if driver:
                driver.quit()
                print("[Browser] Chrome closed")

    print(f"\n[hawkeye-jobs] Grand total unique jobs: {len(all_jobs)}")
    if not all_jobs:
        print("[main] No jobs found from any source")
        return

    new_jobs = filter_new_jobs(all_jobs)
    if not new_jobs:
        print("[main] No new jobs to report")
        return

    _print_jobs(new_jobs)
    print(f"\n[main] Sending email for {len(new_jobs)} new jobs...")
    send_email(new_jobs)

    print("=" * 50)
    print("🦅 hawkeye-jobs done!")
    print("=" * 50)


if __name__ == "__main__":
    main()
