# hawkeye-jobs

Automated Flutter job hunter. Scrapes LinkedIn, Indeed, Bayt.com, NaukriGulf, Google Jobs, and LinkedIn hiring posts, then emails only new listings.

Runs every 2 hours on macOS via LaunchAgent. Deduplicates across sources and across runs.

## Features

- Searches LinkedIn, Indeed, Bayt.com, NaukriGulf, Google Jobs, and LinkedIn hiring posts
- Covers Jordan, Gulf, Europe, North America, and remote
- Keeps Flutter / Dart / mobile titles only
- Only includes jobs posted in the last 48 hours
- Sends an HTML email when there are new jobs
- Remembers seen jobs so you are not notified twice

## Requirements

- macOS (LaunchAgent scheduling)
- Python 3.11+
- Google Chrome (required for Bayt, NaukriGulf, Google Jobs, LinkedIn posts)
- A Gmail account with an [App Password](https://myaccount.google.com/apppasswords)

## Setup

```bash
git clone https://github.com/YOUR_USERNAME/hawkeye-jobs.git
cd hawkeye-jobs
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```
GMAIL_USER=you@gmail.com
GMAIL_PASS=your-16-char-app-password
RECIPIENT_EMAIL=you@gmail.com
```

Create the App Password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) (2-Step Verification must be on).

Run once:

```bash
bash run.sh
```

Or install the every-2-hours schedule:

```bash
bash install_cron.sh
```

Check status:

```bash
launchctl list | grep hawkeye
tail -f hawkeye.log
```

### LinkedIn hiring posts (optional)

LinkedIn blocks anonymous content search. To enable hiring-post scraping, log in once in the profile Chrome uses:

```bash
open -a "Google Chrome" --args --user-data-dir="$HOME/.hawkeye-linkedin-profile"
```

Sign in, close Chrome, then run hawkeye-jobs as usual.

## Uninstall

```bash
bash uninstall_cron.sh
```

## Customize

| What | Where |
|------|--------|
| LinkedIn / Indeed searches | `src/scraper.py` — `LINKEDIN_SEARCHES`, `INDEED_SEARCHES` |
| Bayt searches | `src/bayt.py` — `BAYT_SEARCHES` |
| NaukriGulf searches | `src/naukrigulf.py` — `NAUKRIGULF_SEARCHES` |
| Google Jobs searches | `src/google_jobs.py` — `GOOGLE_SEARCHES` |
| LinkedIn hiring-post phrases | `src/linkedin_posts.py` — `LINKEDIN_POST_SEARCHES` |
| Title exclusions | `src/scraper.py` — `_HARD_EXCLUDE` |
| Schedule interval | `StartInterval` in `~/Library/LaunchAgents/com.hawkeye-jobs.plist` (seconds) |

Reset seen jobs:

```bash
rm -f seen_jobs.json
```

## Project structure

```
hawkeye-jobs/
├── src/
│   ├── main.py            # Pipeline orchestrator
│   ├── scraper.py         # LinkedIn + Indeed (JobSpy)
│   ├── bayt.py            # Bayt.com
│   ├── naukrigulf.py      # NaukriGulf
│   ├── google_jobs.py     # Google Jobs
│   ├── linkedin_posts.py  # LinkedIn hiring posts
│   ├── browser.py         # Shared Chrome driver
│   ├── tracker.py         # Deduplication
│   ├── utils.py           # URL cleaning, date parsing
│   └── email_sender.py    # HTML email + Gmail SMTP
├── docs/ARCHITECTURE.md   # Technical reference
├── .env.example           # Credential template
├── run.sh                 # Loads .env and runs the pipeline
├── install_cron.sh        # macOS LaunchAgent installer
└── uninstall_cron.sh      # Remove the LaunchAgent
```

## Troubleshooting

| Problem | What to try |
|---------|-------------|
| No email | Check `hawkeye.log` and that `.env` is filled in |
| 0 jobs from Bayt / NaukriGulf | Chrome must be installed and up to date |
| Chrome version error | Set `version_main` in `src/browser.py` to match Chrome |
| Gmail auth error | Generate a new App Password |
| LinkedIn posts empty | Log in once using the persistent Chrome profile above |

## License

MIT
