"""
scraper.py — LinkedIn + Indeed scraper via JobSpy
===================================================
Uses the python-jobspy library (no browser required) to search LinkedIn and Indeed.

Search queries come from catalog.expand_searches() based on the user's
terminal choices. Titles are kept only if they match the user's keywords.
"""

from datetime import datetime

from jobspy import scrape_jobs

from filters import is_relevant_job
from utils import is_within_48h, clean_url


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
        print(f"    (filtered {skipped} irrelevant)")
    return result


def scrape_all(linkedin_searches=None, indeed_searches=None, keywords=None) -> list:
    """Run the given LinkedIn and Indeed searches. One failure does not abort the run."""
    linkedin_searches = linkedin_searches or []
    indeed_searches = indeed_searches or []
    keywords = keywords or []
    all_jobs = []
    seen_urls = set()
    print(f"[hawkeye-jobs] Starting search at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    if linkedin_searches:
        print("\n[LinkedIn]")
        for s in linkedin_searches:
            try:
                jobs = scrape_jobs(
                    site_name=["linkedin"],
                    search_term=s["term"],
                    location=s["location"],
                    results_wanted=10,
                    hours_old=48,
                )
                parsed = _parse_jobs(jobs, seen_urls, s["location"], keywords)
                all_jobs.extend(parsed)
                print(f"  '{s['term']}' in {s['location'] or 'Worldwide'}: {len(parsed)} jobs")
            except Exception as e:
                print(f"  '{s['term']}' in {s['location'] or 'Worldwide'}: Error - {e}")

    if indeed_searches:
        print("\n[Indeed]")
        for s in indeed_searches:
            try:
                jobs = scrape_jobs(
                    site_name=["indeed"],
                    search_term=s["term"],
                    location=s["location"],
                    results_wanted=10,
                    hours_old=48,
                    country_indeed=s["country"],
                )
                parsed = _parse_jobs(jobs, seen_urls, s["location"], keywords)
                all_jobs.extend(parsed)
                print(f"  '{s['term']}' in {s['location']}: {len(parsed)} jobs")
            except Exception as e:
                print(f"  '{s['term']}' in {s['location']}: Error - {e}")

    print(f"\n[hawkeye-jobs] Total unique jobs from job boards: {len(all_jobs)}")
    return all_jobs
