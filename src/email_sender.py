"""
email_sender.py — HTML email builder and Gmail sender
=======================================================
Builds a styled HTML digest of new job listings and sends it via Gmail SMTP SSL.

Credentials (from environment variables):
    GMAIL_USER       — sender Gmail address (e.g. you@gmail.com)
    GMAIL_PASS       — Gmail App Password (16-char, NOT your account password)
                       Generate at: myaccount.google.com/apppasswords
    RECIPIENT_EMAIL  — recipient address (defaults to GMAIL_USER if not set)

Email structure:
    ┌─────────────────────────────────┐
    │  🦅 hawkeye-jobs  (dark header) │
    │  N new jobs • timestamp         │
    ├─────────────────────────────────┤
    │  Sources: LinkedIn(3) Bayt(2)…  │  ← coloured pills, one per source
    ├─────────────────────────────────┤
    │  ┌──────────────────────────┐   │
    │  │ Job Title          [Src] │   │  ← one card per job
    │  │ Company - Location       │   │
    │  │ 🔍 keyword  🕒 timestamp │   │
    │  │              Apply Now → │   │
    │  └──────────────────────────┘   │
    │  ...                            │
    └─────────────────────────────────┘

Source badge colours:
    LinkedIn   #0A66C2   Indeed      #003A9B
    Google Jobs #4285F4  Bayt        #E8A020
    NaukriGulf #FF6B35   Glassdoor   #0CAA41
"""

import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime


GMAIL_USER = ""
GMAIL_PASS = ""
RECIPIENT = ""


def _load_credentials():
    global GMAIL_USER, GMAIL_PASS, RECIPIENT
    GMAIL_USER = os.environ.get("GMAIL_USER", "")
    GMAIL_PASS = os.environ.get("GMAIL_PASS", "")
    RECIPIENT = os.environ.get("RECIPIENT_EMAIL", GMAIL_USER)


def _source_color(source: str) -> str:
    """Return the hex colour for a job source badge. Falls back to #555555 for unknown sources."""
    colors = {
        "Linkedin":     "#0A66C2",
        "LinkedIn Post": "#0A66C2",
        "Indeed":       "#003A9B",
        "Google jobs":  "#4285F4",
        "Bayt":         "#E8A020",
        "Naukrigulf":   "#FF6B35",
        "Glassdoor":    "#0CAA41",
    }
    return colors.get(source, "#555555")


def _build_html(jobs: list) -> str:
    """
    Build the full HTML email body as a string.

    Sections:
        1. Dark gradient header — hawk emoji, title, job count, UTC timestamp.
        2. Sources summary bar — one coloured pill per unique source with job count.
        3. Job cards — one card per job with title (linked), company + location,
           source badge, keyword, date_found timestamp, and "Apply Now →" link.
        4. Footer.

    Args:
        jobs : list of job dicts (title, url, description, source, keyword, date_found)

    Returns:
        HTML string ready to attach as text/html MIME part
    """
    now = datetime.utcnow().strftime("%d %b %Y — %H:%M UTC")

    # Group jobs by source
    by_source: dict = {}
    for job in jobs:
        src = job.get("source", "Other")
        by_source.setdefault(src, []).append(job)

    source_pills = "".join(
        f'<span style="display:inline-block;margin:0 4px 4px 0;padding:4px 12px;'
        f'background:{_source_color(src)};color:#fff;border-radius:20px;'
        f'font-size:12px;font-weight:600;">{src} ({len(lst)})</span>'
        for src, lst in by_source.items()
    )

    job_cards = ""
    for job in jobs:
        color = _source_color(job.get("source", ""))
        url   = job.get("url", "#")
        title = job.get("title", "No title")
        desc  = job.get("description", "")
        src   = job.get("source", "")
        kw    = job.get("keyword", "")
        date  = job.get("date_found", "")

        job_cards += f"""
        <div style="background:#ffffff;border:1px solid #e8e8e8;border-left:4px solid {color};
                    border-radius:8px;padding:16px 20px;margin-bottom:12px;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;">
            <div style="flex:1;min-width:200px;">
              <a href="{url}" style="font-size:16px;font-weight:700;color:#1a1a1a;text-decoration:none;
                                     line-height:1.4;display:block;margin-bottom:6px;">
                {title}
              </a>
              <p style="margin:0 0 8px;font-size:13px;color:#666;line-height:1.5;">{desc}</p>
            </div>
            <div style="text-align:right;flex-shrink:0;">
              <span style="display:inline-block;padding:3px 10px;background:{color};color:#fff;
                           border-radius:12px;font-size:11px;font-weight:600;">{src}</span>
            </div>
          </div>
          <div style="display:flex;align-items:center;justify-content:space-between;
                      border-top:1px solid #f0f0f0;padding-top:10px;margin-top:8px;flex-wrap:wrap;gap:6px;">
            <span style="font-size:11px;color:#999;">🔍 {kw}</span>
            <span style="font-size:11px;color:#999;">🕒 {date}</span>
            <a href="{url}" style="font-size:12px;color:{color};font-weight:600;text-decoration:none;">
              Apply Now →
            </a>
          </div>
        </div>
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
    <body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
      <div style="max-width:640px;margin:24px auto;background:#f5f5f5;">

        <!-- Header -->
        <div style="background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);
                    border-radius:12px 12px 0 0;padding:28px 32px;text-align:center;">
          <div style="font-size:32px;margin-bottom:8px;">🦅</div>
          <h1 style="margin:0;color:#ffffff;font-size:24px;font-weight:700;letter-spacing:-0.5px;">
            hawkeye-jobs
          </h1>
          <p style="margin:6px 0 0;color:#a0b4c8;font-size:14px;">
            {len(jobs)} new job{'s' if len(jobs)>1 else ''} found • {now}
          </p>
        </div>

        <!-- Summary bar -->
        <div style="background:#ffffff;border-left:1px solid #e8e8e8;border-right:1px solid #e8e8e8;
                    padding:16px 24px;">
          <p style="margin:0 0 8px;font-size:13px;color:#888;font-weight:600;text-transform:uppercase;
                    letter-spacing:0.5px;">Sources</p>
          <div>{source_pills}</div>
        </div>

        <!-- Job cards -->
        <div style="background:#f5f5f5;padding:16px 16px 8px;">
          {job_cards}
        </div>

        <!-- Footer -->
        <div style="background:#ffffff;border:1px solid #e8e8e8;border-radius:0 0 12px 12px;
                    padding:16px 24px;text-align:center;">
          <p style="margin:0;font-size:12px;color:#aaa;line-height:1.6;">
            Sent by <strong>hawkeye-jobs</strong><br>
            <a href="https://github.com" style="color:#4285F4;text-decoration:none;">View on GitHub</a>
          </p>
        </div>

      </div>
    </body>
    </html>
    """
    return html


def send_email(jobs: list):
    """
    Send the HTML job digest via Gmail SMTP SSL (port 465).

    The email contains both a text/plain fallback and a text/html part.
    Credentials are read from the GMAIL_USER and GMAIL_PASS environment variables.

    Skips sending if:
        - jobs list is empty
        - GMAIL_USER or GMAIL_PASS is not set

    Raises:
        smtplib.SMTPException if the send fails (exception is printed and re-raised
        so it appears in the log).

    Args:
        jobs : list of new job dicts to include in the digest
    """
    _load_credentials()
    if not jobs:
        print("[email] No new jobs — skipping email")
        return

    if not GMAIL_USER or not GMAIL_PASS:
        print("[email] ERROR: GMAIL_USER or GMAIL_PASS not set")
        print("[email] Jobs were still printed above. Copy .env.example to .env to enable email.")
        return

    subject = f"🦅 hawkeye-jobs — {len(jobs)} new job{'s' if len(jobs)>1 else ''} found"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"hawkeye-jobs <{GMAIL_USER}>"
    msg["To"]      = RECIPIENT

    # Plain text fallback
    plain = f"hawkeye-jobs — {len(jobs)} new jobs found\n\n"
    for job in jobs:
        plain += f"• {job['title']}\n  {job['url']}\n  {job.get('description','')}\n\n"
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(_build_html(jobs), "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, RECIPIENT, msg.as_string())
        print(f"[email] Sent successfully to {RECIPIENT}")
    except Exception as e:
        print(f"[email] Failed to send: {e}")
        print("[email] Jobs were still printed above. Check GMAIL_USER / GMAIL_PASS in .env.")
