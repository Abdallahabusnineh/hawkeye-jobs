# hawkeye-jobs

Terminal job hunter. You choose the search keywords, countries, and sources; it scrapes LinkedIn, Indeed, Bayt.com, NaukriGulf, Google Jobs, and LinkedIn hiring posts, then emails only new listings.

Deduplicates across sources and across runs. Nothing runs in the background — you start it when you want results.

## Features

- Interactive terminal setup: keywords, locations, sources
- Remembers your last choices in `config.json` (`Y` to reuse, `n` to change)
- Searches LinkedIn, Indeed, Bayt.com, NaukriGulf, Google Jobs, and LinkedIn hiring posts
- Keeps titles that match your keywords
- Only includes jobs posted in the last 48 hours
- Prints new jobs in the terminal and emails an HTML digest
- Remembers seen jobs so you are not notified twice

## Requirements

- Python 3.11+
- Google Chrome (required for Bayt, NaukriGulf, Google Jobs, LinkedIn posts)
- A Gmail account with an [App Password](https://myaccount.google.com/apppasswords) if you want email

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

## Run

```bash
bash run.sh
```

Or:

```bash
source venv/bin/activate
python src/main.py
```

You will be asked for:

1. Search keywords (comma-separated), e.g. `Python Developer, Django`
2. Locations (numbers or `all`)
3. Sources (numbers or `all`)

The next run shows those settings and asks `Use these? [Y/n]`.

Bayt.com and NaukriGulf only cover Jordan/Gulf. Choosing UK or Remote for those sources is skipped automatically.

### LinkedIn hiring posts (optional)

LinkedIn blocks anonymous content search. Log in once in the profile Chrome uses:

```bash
open -a "Google Chrome" --args --user-data-dir="$HOME/.hawkeye-linkedin-profile"
```

Sign in, close Chrome, then run hawkeye-jobs and include **LinkedIn hiring posts**.

## Reset

```bash
rm -f seen_jobs.json    # notify about listings again
rm -f config.json       # re-enter keywords / locations / sources
```

If an older version installed a macOS LaunchAgent:

```bash
bash uninstall_cron.sh
```

## Project structure

```
hawkeye-jobs/
├── src/
│   ├── main.py            # Prompts, then runs the pipeline
│   ├── settings.py        # Terminal prompts + config.json
│   ├── catalog.py         # Location/source → per-site queries
│   ├── filters.py         # Title / hiring-post matching
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
├── config.example.json    # Sample search settings
├── .env.example           # Credential template
└── run.sh                 # Loads .env and runs the pipeline
```

## Troubleshooting

| Problem | What to try |
|---------|-------------|
| No email | Jobs still print in the terminal. Fill in `.env` |
| 0 jobs from Bayt / NaukriGulf | Chrome must be installed; those sites are Gulf/Jordan only |
| Chrome version error | Set `version_main` in `src/browser.py` to match Chrome |
| Gmail auth error | Generate a new App Password |
| LinkedIn posts empty | Log in once using the persistent Chrome profile above |

## License

MIT
