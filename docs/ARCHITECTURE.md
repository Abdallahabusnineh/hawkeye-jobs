# hawkeye-jobs — Architecture & Code Reference

Automated Flutter job hunter. Runs every 2 hours via macOS LaunchAgent, scraping LinkedIn, Indeed, Bayt.com, NaukriGulf, and Google Jobs, then emailing new listings.

---

## Directory Layout

```
hawkeye-jobs/
├── src/
│   ├── main.py          # Orchestrator — runs full pipeline
│   ├── scraper.py       # LinkedIn + Indeed via JobSpy
│   ├── bayt.py          # Bayt.com via Selenium
│   ├── naukrigulf.py    # NaukriGulf via Selenium
│   ├── google_jobs.py   # Google Jobs via Selenium
│   ├── linkedin_posts.py # LinkedIn content/post search via Selenium (needs login)
│   ├── browser.py       # Shared Chrome driver factory
│   ├── tracker.py       # Cross-run deduplication (seen_jobs.json)
│   ├── utils.py         # Shared: clean_url(), is_within_48h()
│   └── email_sender.py  # HTML email builder + Gmail SMTP sender
├── .env.example         # Credential template (copy to .env)
├── run.sh               # Loads .env and runs the pipeline
├── requirements.txt
└── CLAUDE.md
```

`seen_jobs.json` and `hawkeye.log` are created at runtime and gitignored.

---

## Pipeline Flow (`src/main.py`)

```
scrape_all()                    → LinkedIn + Indeed (JobSpy, no browser)
    ↓
create_driver()                 → One Chrome instance shared across all Selenium scrapers
scrape_bayt(seen_urls, driver)
scrape_naukrigulf(seen_urls, driver)
scrape_google_jobs(seen_urls, driver)
scrape_linkedin_posts(seen_urls, driver)   → LinkedIn hiring posts (content search, needs logged-in profile)
driver.quit()
    ↓
filter_new_jobs(all_jobs)       → Remove already-emailed jobs (seen_jobs.json)
    ↓
send_email(new_jobs)            → Gmail SMTP SSL, HTML email
```

`seen_urls` is a Python `set` built from all JobSpy results before Selenium scrapers run. Each Selenium scraper receives and mutates the same set — preventing cross-source duplicates within a single run (e.g. the same job appearing on both Bayt and Google Jobs).

---

## Source Files

### `src/scraper.py` — LinkedIn + Indeed

Uses the `python-jobspy` library to scrape LinkedIn and Indeed. No browser required.

**Key constants:**

```python
LINKEDIN_SEARCHES  # list of {term, location} dicts
INDEED_SEARCHES    # list of {term, location, country} dicts
GOOGLE_SEARCHES    # list of {term, location} dicts (passed to JobSpy — currently unused, JobSpy Google is broken)

_HARD_EXCLUDE = [
    "qa engineer", "qa lead", "qa manager", "quality assurance",
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

_REQUIRED = ["flutter", "dart", "cross platform", "cross-platform"]
```

**`_is_flutter_job(title: str) -> bool`**

Filters job titles. Logic:
1. If any `_HARD_EXCLUDE` substring is found in the lowercased title → reject
2. If any `_REQUIRED` keyword is found → accept
3. If `\bmobile\b` matches (word boundary regex) → accept — uses `re.search` not `in` to prevent "automobile" from matching
4. Otherwise → reject

**`_parse_jobs(jobs, seen_urls, location) -> list`**

Converts a JobSpy DataFrame to the standard job dict format:
- Calls `clean_url()` to strip UTM params before dedup check
- Calls `_is_flutter_job()` for title filtering
- Calls `is_within_48h()` for date filtering
- Returns list of dicts with keys: `title`, `url`, `description`, `source`, `keyword`, `location`, `published`, `date_found`

**`scrape_all() -> list`**

Iterates `LINKEDIN_SEARCHES` and `INDEED_SEARCHES`, calls `scrape_jobs()` with `hours_old=48`, returns combined list. Catches per-search exceptions so one failure doesn't abort the whole run.

---

### `src/browser.py` — Shared Chrome Driver

```python
def create_driver(headless=False, user_data_dir=None) -> uc.Chrome
```

Creates a `undetected-chromedriver` instance. `user_data_dir` (when set) points Chrome at a persistent profile directory (`--user-data-dir`) so a logged-in session survives across runs — used by `linkedin_posts.py` for the LinkedIn content search. Key options:
- `--disable-blink-features=AutomationControlled` — hides Selenium signature
- `version_main=149` — must match the installed Chrome version (check with `google-chrome --version`)
- `headless=False` is **required** — Bayt.com uses Cloudflare which blocks headless Chrome

Called once in `main.py`, the same driver instance is passed to all three Selenium scrapers, then `.quit()` is called in the `finally` block.

**To update Chrome version:** change `version_main=149` to match your Chrome version.

---

### `src/bayt.py` — Bayt.com Scraper

Covers: Jordan, UAE, Saudi Arabia, Kuwait, Qatar, Oman, Bahrain.

**URL pattern:**
```
https://www.bayt.com/en/{country_slug}/jobs/{term_slug}-jobs/
```
Example: `https://www.bayt.com/en/uae/jobs/flutter-developer-jobs/`

**HTML selectors (as of 2026):**
| Data | Selector |
|------|----------|
| Job card | `li[data-job-id]` |
| Title + URL | `.jb-title a, h2 a, h3 a` |
| Company | `a.t-default.t-bold` |
| Location | `.job-company-location-wrapper .t-mute span` |
| Date posted | `[data-automation-id='job-active-date']` |

**Cloudflare bypass:** navigates to URL, then `time.sleep(8)` to let the Cloudflare JS challenge resolve. Must use `headless=False`.

**`_ensure_window(driver)`** — closes any extra browser tabs opened during navigation, switches back to tab 0. Called before and after each `driver.get()`.

**`_parse_bayt_page(html, seen_urls, location)`** — parses raw HTML with BeautifulSoup. Applies `_is_flutter_job()` and `is_within_48h()`.

---

### `src/naukrigulf.py` — NaukriGulf Scraper

Covers: Gulf-wide, UAE, Saudi Arabia, Kuwait, Qatar, Bahrain.

**URL pattern:**
```python
def _build_url(keyword, location_slug):
    kw = keyword.replace(" ", "-")
    if location_slug not in ("gulf-region", ""):
        return f"https://www.naukrigulf.com/{kw}-jobs-in-{location_slug}"
    return f"https://www.naukrigulf.com/{kw}-jobs"
```
Example: `https://www.naukrigulf.com/flutter-developer-jobs-in-uae`

**HTML selectors (as of 2026):**
| Data | Selector |
|------|----------|
| Job card | `.ng-box.srp-tuple` |
| Link + title container | `a.info-position` |
| Title text | `.designation-title` (inside the link) |
| Company | `a.info-org` |
| Location | `li.info-loc span:last-child` |
| Date posted | `span.time` |

**Wait:** `time.sleep(6)` per page — NaukriGulf is an Angular SPA; needs time to render.

**`_ensure_main_window(driver)`** — NaukriGulf opens extra tabs on some navigations. This helper closes all tabs except the first and switches back to it. Called before and after each `driver.get()`.

---

### `src/google_jobs.py` — Google Jobs Scraper

Covers: Gulf, Europe, Americas, Remote/Worldwide.

**URL pattern:**
```python
def _build_url(term, location):
    q = f"{term} jobs {location}".strip().replace(" ", "+")
    return f"https://www.google.com/search?q={q}&ibp=htl;jobs&hl=en"
```
`ibp=htl;jobs` activates the Google Jobs panel.

**How extraction works:**

All 10 job cards and their apply links are embedded in the initial HTML — no clicking required. The page is parsed by zipping parallel element lists by index:

```python
titles    = soup.select(".PUpOsf")         # job titles (10 elements)
companies = soup.select(".a3jPc")          # company names
locations = soup.select(".FqK3wc")         # location text
link_grps = soup.select('[role="list"].EDblX')  # apply link groups (10 lists)
```

Each `link_grps[i]` contains multiple `<a>` tags (one per platform hosting the job). The scraper takes the first non-Google URL from each group.

**Google domains skipped:**
```python
SKIP_DOMAINS = ["google.com", "google.jo", "google.ae", "google.co", "accounts.google", "goo.gl"]
```

**Note:** Google Jobs doesn't expose a `date_posted` field in its HTML, so `published` is set to `""`. Jobs are included regardless of age (no 48h filter here — Google Jobs already surfaces recent jobs).

**Wait:** `time.sleep(7)` per search for the jobs panel to render.

---

### `src/linkedin_posts.py` — LinkedIn Content/Post Scraper

Scrapes LinkedIn's **global content search** (feed posts) — *not* the Jobs board that `scraper.py`/JobSpy reads. Targets hiring posts like "We're hiring a Flutter developer!".

**Authentication (required):** LinkedIn shows an auth wall to anonymous visitors. The scraper reuses a **persistent Chrome profile** created by `create_driver(user_data_dir=...)`. `main.py` sets this from `LINKEDIN_PROFILE_DIR` (default `~/.hawkeye-linkedin-profile`). Log into LinkedIn **once by hand** in that profile; the session cookie is reused every run. If logged out, the scraper detects the login/authwall redirect, prints instructions, and returns `[]` without aborting the pipeline.

**First-time setup:**
```bash
# Open Chrome with the same profile the automation uses, sign into LinkedIn, then close it.
open -a "Google Chrome" --args --user-data-dir="$HOME/.hawkeye-linkedin-profile"
```

**URL pattern:**
```
https://www.linkedin.com/search/results/content/?keywords=<enc>&datePosted=%22past-24h%22&sortBy=%22date_posted%22
```
`datePosted=past-24h` keeps posts fresh; `sortBy=date_posted` surfaces newest first. Cross-run dedup (`seen_jobs.json`) prevents re-emailing the same post.

**Search phrases (`LINKEDIN_POST_SEARCHES`):** `"were hiring flutter"`, `hiring flutter developer`, `flutter developer wanted`, `looking for flutter developer`.

**Relevance filter (`_is_hiring_flutter_post`):** does **not** use `_is_flutter_job()` (post text is prose, not a title). A post is kept only if its text mentions `flutter` **and** contains a hiring signal (`hiring`, `looking for`, `wanted`, `vacancy`, `open position`, …). Recency is enforced by the URL filter plus `_post_within_48h()` for LinkedIn's compact times (`5h`, `1d`, `2w`).

**HTML selectors (as of 2026 — LinkedIn changes its DOM often):**
| Data | Selector |
|------|----------|
| Post card | `div[data-urn*="urn:li:activity"]` (fallback `.feed-shared-update-v2`) |
| Post URL | built from `data-urn` → `/feed/update/<urn>/` |
| Post text | `.update-components-text, .feed-shared-update-v2__description, .feed-shared-inline-show-more-text` |
| Author | `.update-components-actor__title, .update-components-actor__name` |
| Relative time | `.update-components-actor__sub-description` |

**Wait / scroll:** 5s initial render, then 4 scrolls × 2s to lazy-load more posts.

**Note:** `source` is `"LinkedIn Post"`; `title` is the post's opening hook (truncated to ~90 chars); `description` is `"{author} · LinkedIn hiring post"`.

---

### `src/utils.py` — Shared Utilities

**`clean_url(url: str) -> str`**

Strips UTM and tracking query parameters so the same job from different sources maps to the same canonical URL for deduplication.

Strips: `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, `utm_term`, `source`, `ref`, `referer`, `tracking`

Example: `https://indeed.com/viewjob?jk=abc123&utm_source=google` → `https://indeed.com/viewjob?jk=abc123`

**`is_within_48h(date_str: str) -> bool`**

Returns `True` if the job was posted within 48 hours. `MAX_AGE_HOURS = 48`.

Handles multiple formats:
| Input format | Example | Behavior |
|---|---|---|
| Empty / None / NaN | `""`, `"none"`, `"nan"` | `True` (benefit of the doubt) |
| Immediate | `"just now"`, `"today"`, `"moments ago"` | `True` |
| Hours ago | `"3 hours ago"` | `True` if ≤ 48 |
| Days ago | `"1 day ago"`, `"2 days ago"` | `True` if ≤ 2 |
| Yesterday | `"yesterday"` | `True` |
| Old markers | `"30+ days ago"`, `"3 weeks ago"`, `"1 month ago"` | `False` |
| Day-month | `"29 Apr"`, `"1 May"` | Compare against now, `True` if ≤ 48h |
| ISO date | `"2026-05-04"` | Compare against now, `True` if ≤ 48h |
| Unparseable | anything else | `True` |

---

### `src/tracker.py` — Cross-Run Deduplication

Persists job IDs across runs in `seen_jobs.json` (root of the project).

**`seen_jobs.json` format:**
```json
{
  "seen": ["md5hash1", "md5hash2", ...],
  "count": 1234
}
```

**`_job_id(job: dict) -> str`**

Canonical ID = MD5 of the cleaned URL (after stripping UTM params and trailing slash). This means:
- Same job URL from two different scrapers → same ID → deduplicated
- UTM-param variant of same URL → same ID

**`filter_new_jobs(jobs: list) -> list`**

1. Loads `seen_jobs.json` (treats a missing file as an empty set)
2. For each job, computes its ID; keeps only jobs whose ID is not in the seen set
3. Adds new IDs to the seen set and saves back to `seen_jobs.json`
4. Returns only the new jobs

**Resetting:** `rm -f seen_jobs.json` — forces re-notification of all current listings on next run.

---

### `src/email_sender.py` — Email Builder + Sender

Sends an HTML email via Gmail SMTP SSL (port 465).

**Credentials (from environment variables):**
| Variable | Purpose |
|---|---|
| `GMAIL_USER` | Sender Gmail address |
| `GMAIL_PASS` | Gmail App Password (not account password) |
| `RECIPIENT_EMAIL` | Recipient address (defaults to `GMAIL_USER`) |

**`_build_html(jobs) -> str`**

Builds an HTML email with:
- Dark gradient header with hawk emoji and job count
- Source summary pills (colored by platform)
- One card per job with: title (clickable link), company + location, source badge, "Apply Now →" link, date found timestamp

**Source colors:**
| Source | Color |
|---|---|
| LinkedIn | `#0A66C2` (LinkedIn blue) |
| Indeed | `#003A9B` (Indeed dark blue) |
| Google Jobs | `#4285F4` (Google blue) |
| Bayt | `#E8A020` (amber) |
| NaukriGulf | `#FF6B35` (orange) |
| Glassdoor | `#0CAA41` (green) |

**`send_email(jobs: list)`**

Sends both `text/plain` (fallback) and `text/html` parts. Raises the SMTP exception if sending fails (so the error appears in the log).

---

## Job Dict Schema

All scrapers produce job dicts with this shape:

```python
{
    "title":       str,   # Job title as scraped
    "url":         str,   # Direct apply URL (UTM-cleaned before dedup)
    "description": str,   # "{company} - {location}" or just location
    "source":      str,   # "Linkedin", "Indeed", "Bayt", "NaukriGulf", "Google Jobs"
    "keyword":     str,   # Search term used (currently always "")
    "location":    str,   # Search location (e.g. "United Arab Emirates")
    "published":   str,   # Raw date string from the source
    "date_found":  str,   # UTC timestamp when the scraper ran
}
```

---

## Search Coverage

| Source | Regions |
|---|---|
| LinkedIn | Jordan, UAE, Saudi, Kuwait, Qatar, Oman, Bahrain, UK, Germany, Netherlands, France, USA, Canada, Remote |
| Indeed | UAE, Saudi, Kuwait, Qatar, Oman, Bahrain, UK, Germany, Netherlands, USA, Canada |
| Bayt | Jordan, UAE, Saudi, Kuwait, Qatar, Oman, Bahrain |
| NaukriGulf | Gulf-wide, UAE, Saudi, Kuwait, Qatar, Bahrain |
| Google Jobs | UAE, Saudi, Kuwait, Qatar, Jordan, UK, Germany, Netherlands, USA, Canada, Remote/Worldwide |
| LinkedIn Posts | Global (content search — hiring posts, not location-scoped) |

---

## Common Maintenance Tasks

### Update Chrome version
If `undetected-chromedriver` fails with version mismatch:
1. Check Chrome version: `google-chrome --version` (or check in Chrome → About)
2. Update `version_main=149` in `src/browser.py` to match

### Add a new search term or location
- **LinkedIn / Indeed:** add a dict to `LINKEDIN_SEARCHES` or `INDEED_SEARCHES` in `src/scraper.py`
- **Bayt:** add a `(term_slug, country_slug)` tuple to `BAYT_SEARCHES` in `src/bayt.py`
- **NaukriGulf:** add a `(keyword, location_slug, "")` tuple to `NAUKRIGULF_SEARCHES` in `src/naukrigulf.py`
- **Google Jobs:** add a `(term, location)` tuple to `GOOGLE_SEARCHES` in `src/google_jobs.py`
- **LinkedIn Posts:** add a phrase string to `LINKEDIN_POST_SEARCHES` in `src/linkedin_posts.py` (tune `_HIRING_SIGNALS` to widen/narrow what counts as a hiring post)

### Add/remove excluded job titles
Edit `_HARD_EXCLUDE` in `src/scraper.py`. The same list is used for all sources because `bayt.py`, `naukrigulf.py`, and `google_jobs.py` all import `_is_flutter_job` from `scraper.py`.

### Add a new required keyword
Edit `_REQUIRED` in `src/scraper.py`. Currently: `["flutter", "dart", "cross platform", "cross-platform"]`.

### Fix broken selectors
Bayt and NaukriGulf change their HTML periodically. If a scraper returns 0 jobs:
1. Open the site in a browser
2. Inspect a job card's HTML
3. Update the `soup.select(...)` calls in the relevant `_parse_*_page()` function

### Google Jobs returns 0
If `.PUpOsf` or `[role="list"].EDblX` selectors stop working:
1. Open `https://www.google.com/search?q=Flutter+Developer+jobs+UAE&ibp=htl;jobs&hl=en` in Chrome
2. Inspect the job title elements and the apply-link list containers
3. Update the selectors in `src/google_jobs.py` → `_parse_google_page()`

---

## Dependencies

```
python-jobspy==1.1.80    # LinkedIn + Indeed scraper
pandas                   # Required by jobspy
requests                 # HTTP utilities
beautifulsoup4           # HTML parsing for Bayt, NaukriGulf, Google Jobs
undetected-chromedriver  # Selenium wrapper that bypasses bot detection
setuptools               # Required on Python 3.12+ (distutils removed)
```

Install: `pip install -r requirements.txt`

---

## Running Locally

```bash
cp .env.example .env   # fill in GMAIL_USER / GMAIL_PASS / RECIPIENT_EMAIL
bash run.sh
```

Logs: `hawkeye.log` in project root.

macOS LaunchAgent (runs every 2 hours):
```bash
launchctl list | grep hawkeye          # check status
launchctl unload ~/Library/LaunchAgents/com.hawkeye-jobs.plist
launchctl load  ~/Library/LaunchAgents/com.hawkeye-jobs.plist
```
