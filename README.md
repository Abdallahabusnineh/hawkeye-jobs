# hawkeye-jobs

Terminal job hunter. You choose the search keywords, countries, and sources; it scrapes LinkedIn, Indeed, Bayt.com, NaukriGulf, Google Jobs, and LinkedIn hiring posts, then emails only new listings.

Deduplicates across sources and across runs. Nothing runs in the background — you start it when you want results.

## Features

- Interactive checkboxes: arrows move, space selects, enter confirms
- Country list from a public API on every `bash run.sh` run — type to search, space to select
- Remembers your last choices
- Searches LinkedIn, Indeed, Bayt.com, NaukriGulf, Google Jobs, and LinkedIn hiring posts
- Keeps titles that match your keywords
- Only includes jobs posted in the last 7 days
- Prints new jobs in the terminal and emails an HTML digest
- Remembers every job it has found (with a status: new / saved / applied / dismissed) so you are not notified twice
- Waits politely between requests to each site so your accounts and connection are not flagged
- Ctrl+C stops a search cleanly; jobs found so far are kept

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

1. Search keywords (type them, comma-separated), e.g. `Flutter Developer, Mobile Developer`
2. Countries — type to filter, space to select, enter, then Yes/No to confirm those countries
3. Websites — space to select, `a` all, `d` remove all, enter, then Yes/No to confirm those sites
4. Next run: Yes/No to reuse saved settings (↑↓ and enter)

Bayt.com and NaukriGulf only cover Jordan/Gulf. Other countries still search LinkedIn, Indeed, and Google Jobs.

## Where your data lives

Everything hawkeye-jobs saves is kept in one folder on your computer:

| OS | Folder |
|---|---|
| macOS | `~/Library/Application Support/hawkeye-jobs` |
| Windows | `C:\Users\<you>\AppData\Local\hawkeye-jobs` |
| Linux | `~/.local/share/hawkeye-jobs` |

It contains `hawkeye.db` (jobs and searches), `config.json` (your search settings), `hawkeye.log` (attach this when reporting a problem), and `linkedin-profile/` (the Chrome profile used for LinkedIn posts).

Set `HAWKEYE_DATA_DIR` to use a different folder. If you used hawkeye-jobs before this change, your old `config.json` and `seen_jobs.json` are imported automatically the first time you run it. The old files are left in place.

**Reset** (forget all jobs and get notified again): delete `hawkeye.db*` (all three files: `hawkeye.db`, `hawkeye.db-wal`, `hawkeye.db-shm`) from that folder. If you used an older version, also delete the old `seen_jobs.json` in the project folder, otherwise it is imported again on the next run and those jobs stay hidden.

**LinkedIn posts** need a one-time login. Open Chrome with the profile the tool uses, sign in to LinkedIn, and close it:

    open -a "Google Chrome" --args --user-data-dir="$HOME/Library/Application Support/hawkeye-jobs/linkedin-profile"

If you already have `~/.hawkeye-linkedin-profile` from an older version, it keeps being used.

**Chrome** is found automatically on macOS, Windows, and Linux, and its version is detected for you. Set `HAWKEYE_CHROME_MAJOR` only if detection picks the wrong version.

## Reset

See [Where your data lives](#where-your-data-lives) to forget all jobs.

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
│   ├── picker.py          # Arrow/space checkbox menus + searchable country list
│   ├── catalog.py         # Country/source → per-site queries
│   ├── countries.py       # REST Countries API + cache
│   ├── filters.py         # Title / hiring-post matching
│   ├── scraper.py         # LinkedIn + Indeed (JobSpy)
│   ├── bayt.py            # Bayt.com
│   ├── naukrigulf.py      # NaukriGulf
│   ├── google_jobs.py     # Google Jobs
│   ├── linkedin_posts.py  # LinkedIn hiring posts
│   ├── browser.py         # Chrome discovery + shared driver
│   ├── pipeline.py        # One search run: lanes, events, cancel
│   ├── store.py           # SQLite jobs, statuses, runs
│   ├── paths.py           # Data directory and file locations
│   ├── logs.py            # Rotating log file
│   ├── utils.py           # URL cleaning, date parsing
│   └── email_sender.py    # HTML email + Gmail SMTP
├── docs/ARCHITECTURE.md   # Technical reference
├── config.example.json    # Sample search settings
├── .env.example           # Credential template
└── run.sh                 # Loads .env and runs the pipeline
```

## Troubleshooting

| Problem                       | What to try                                                |
| ----------------------------- | ---------------------------------------------------------- |
| No email                      | Jobs still print in the terminal. Fill in `.env`           |
| 0 jobs from Bayt / NaukriGulf | Chrome must be installed; those sites are Gulf/Jordan only |
| Chrome version error          | Set `HAWKEYE_CHROME_MAJOR` to your Chrome major version    |
| Gmail auth error              | Generate a new App Password                                |
| LinkedIn posts empty          | Log in once using the LinkedIn Chrome profile (see above)  |

## License

MIT
