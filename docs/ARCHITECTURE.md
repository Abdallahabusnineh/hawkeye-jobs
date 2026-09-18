# hawkeye-jobs — Architecture & Code Reference

Terminal job hunter. The user picks keywords, locations, and sources; it scrapes LinkedIn, Indeed, Bayt.com, NaukriGulf, Google Jobs, and LinkedIn hiring posts, then prints and emails new listings.

---

## Directory Layout

```
hawkeye-jobs/
├── src/
│   ├── main.py          # Prompts, then runs the pipeline
│   ├── settings.py      # Terminal prompts + config.json
│   ├── picker.py        # Arrow/space checkbox and yes/no menus
│   ├── catalog.py       # Location/source → per-site queries
│   ├── filters.py       # Title / hiring-post matching
│   ├── scraper.py       # LinkedIn + Indeed via JobSpy
│   ├── bayt.py          # Bayt.com via Selenium
│   ├── naukrigulf.py    # NaukriGulf via Selenium
│   ├── google_jobs.py   # Google Jobs via Selenium
│   ├── linkedin_posts.py # LinkedIn content/post search via Selenium (needs login)
│   ├── browser.py       # Shared Chrome driver factory
│   ├── tracker.py       # Cross-run deduplication (seen_jobs.json)
│   ├── utils.py         # Shared: clean_url(), is_within_48h()
│   └── email_sender.py  # HTML email builder + Gmail SMTP sender
├── config.example.json  # Sample search settings
├── .env.example         # Credential template
├── run.sh               # Loads .env and runs the pipeline
├── requirements.txt
└── CLAUDE.md
```

`config.json`, `seen_jobs.json`, and `hawkeye.log` are created at runtime and gitignored.

---

## Pipeline Flow (`src/main.py`)

```
collect_settings()              → reuse config.json or prompt in the terminal
expand_searches()               → per-site queries from keywords × locations
    ↓
scrape_all()                    → LinkedIn + Indeed (JobSpy, no browser)
    ↓
create_driver()                 → Chrome only if a browser source is selected
scrape_bayt / naukrigulf / google_jobs / linkedin_posts
driver.quit()
    ↓
filter_new_jobs(all_jobs)       → Remove already-seen jobs (seen_jobs.json)
    ↓
print jobs + send_email()       → Terminal list + Gmail HTML digest
```

`seen_urls` is a Python `set` built from all JobSpy results before Selenium scrapers run. Each Selenium scraper receives and mutates the same set — preventing cross-source duplicates within a single run (e.g. the same job appearing on both Bayt and Google Jobs).

---

## Source Files

### `src/settings.py` — Terminal prompts + saved config

`collect_settings()` loads `config.json` if present and shows a Yes/No picker (`↑↓` + enter). Yes reuses it. No (or no saved file) prompts for keywords (typed), then checkbox lists for locations and websites (`↑↓` move, space select, `a` all, enter confirm). Writes `config.json`. If stdin is not a TTY, it falls back to numbered lists.

`config.json` shape:

```json
{
  "keywords": ["Flutter Developer"],
  "locations": ["jordan", "uae"],
  "sources": ["linkedin", "indeed"]
}
```

### `src/catalog.py` — Expand user choices into site queries

`LOCATIONS` maps a stable id (`jordan`, `uae`, `remote`, …) to per-site strings/slugs. Sources that do not support a country (Bayt/NaukriGulf outside the Gulf, Indeed for Remote) are skipped.

`expand_searches(keywords, location_ids, source_ids)` returns:

```python
{
  "linkedin": [{"term", "location"}, ...],
  "indeed": [{"term", "location", "country"}, ...],
  "bayt": [(term_slug, country_slug), ...],
  "naukrigulf": [(keyword, location_slug), ...],
  "google_jobs": [(term, location), ...],
  "linkedin_posts": ["hiring {keyword}", "looking for {keyword}", ...],
}
```

### `src/filters.py` — Keyword matching

`keyword_stems()` drops generic role words (`developer`, `engineer`, …). `is_relevant_job(title, keywords)` keeps a title if a stem matches as a whole word (`mobile` will not match `automobile`). `is_hiring_post(text, keywords)` additionally requires a hiring signal.

### `src/scraper.py` — LinkedIn + Indeed

Uses the `python-jobspy` library. No browser required. Search lists are passed in from `expand_searches()`, not hardcoded.

**`_parse_jobs(jobs, seen_urls, location, keywords) -> list`**

- `clean_url()` before dedup
- `is_relevant_job()` for title filtering
- `is_within_48h()` for date filtering

**`scrape_all(linkedin_searches, indeed_searches, keywords) -> list`**

Calls `scrape_jobs()` with `hours_old=48`. One failed search does not abort the run.

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

**`_parse_bayt_page(html, seen_urls, location, keywords)`** — parses raw HTML with BeautifulSoup. Applies `is_relevant_job()` and `is_within_48h()`.

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

**Search phrases:** built from the user's keywords, e.g. `hiring Flutter Developer`, `looking for Flutter Developer`.

**Relevance filter (`is_hiring_post`):** does **not** use `is_relevant_job()` (post text is prose, not a title). A post is kept only if its text mentions a user keyword stem **and** contains a hiring signal (`hiring`, `looking for`, `wanted`, `vacancy`, `open position`, …). Recency is enforced by the URL filter plus `_post_within_48h()` for LinkedIn's compact times (`5h`, `1d`, `2w`).

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

Locations are chosen in the terminal. Per-source support is defined in `src/catalog.py`:

| Source | Supported locations |
|---|---|
| LinkedIn | Jordan, UAE, Saudi, Kuwait, Qatar, Oman, Bahrain, UK, Germany, Netherlands, France, USA, Canada, Remote |
| Indeed | Jordan, UAE, Saudi, Kuwait, Qatar, Oman, Bahrain, UK, Germany, Netherlands, France, USA, Canada |
| Bayt | Jordan, UAE, Saudi, Kuwait, Qatar, Oman, Bahrain |
| NaukriGulf | UAE, Saudi, Kuwait, Qatar, Bahrain |
| Google Jobs | All of the above including Remote |
| LinkedIn Posts | Global (content search — not location-scoped) |

---

## Common Maintenance Tasks

### Update Chrome version
If `undetected-chromedriver` fails with version mismatch:
1. Check Chrome version: `google-chrome --version` (or check in Chrome → About)
2. Update `version_main=149` in `src/browser.py` to match

### Add a country or source mapping
Edit `LOCATIONS` / `SOURCES` in `src/catalog.py`.

### Tune title matching
Edit `keyword_stems()` stopwords or `is_hiring_post()` signals in `src/filters.py`.

### Fix broken selectors
Bayt and NaukriGulf change their HTML periodically. If a scraper returns 0 jobs:
1. Open the site in a browser
2. Inspect a job card's HTML
3. Update the `soup.select(...)` calls in the relevant `_parse_*_page()` function

### Google Jobs returns 0
If `.PUpOsf` or `[role="list"].EDblX` selectors stop working:
1. Open a Google Jobs URL for any keyword in Chrome
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
bash run.sh            # prompts for keywords, locations, sources
```

To change saved searches: pick **No** on the reuse menu, or `rm -f config.json`.
