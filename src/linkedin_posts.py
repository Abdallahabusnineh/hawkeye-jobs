"""
linkedin_posts.py — LinkedIn content/post search scraper via Selenium
======================================================================
Scrapes LinkedIn's global CONTENT search (feed posts), NOT the Jobs board.

Why this is separate from scraper.py:
    scraper.py uses JobSpy, which only reads the LinkedIn *Jobs* board. This
    module targets LinkedIn's post/content search — the results you get from
    https://www.linkedin.com/search/results/content/?keywords=...
    These are feed posts where recruiters/founders write things like
    "We're hiring a Flutter developer!". They are not structured job listings,
    so they need their own parser and their own relevance filter.

Authentication (REQUIRED):
    LinkedIn shows an auth wall to anonymous visitors on content search. This
    scraper reuses a persistent Chrome profile (paths.linkedin_profile_dir()).
    Log into LinkedIn ONCE by hand in that profile; the session cookie is then
    reused on later runs. If the session is missing or expired,
    fetch_linkedin_posts() raises LinkedInLoginRequired and the pipeline skips
    the remaining post searches.

URL pattern:
    https://www.linkedin.com/search/results/content/?keywords=<enc>
        &datePosted=%22past-24h%22&sortBy=%22date_posted%22
    - datePosted=past-24h keeps posts fresh; cross-run
      dedup in tracker.py prevents re-emailing the same post.
    - sortBy=date_posted surfaces the newest posts first.

HTML selectors (last verified 2026 — LinkedIn obfuscates/changes its DOM often):
    Post container : div[data-urn*="urn:li:activity"]  (also .feed-shared-update-v2)
    Post URL       : built from the data-urn → /feed/update/<urn>/
    Author         : .update-components-actor__title
    Post text      : .update-components-text, .feed-shared-update-v2__description
    Relative time  : .update-components-actor__sub-description

    ⚠ If this returns 0 posts while you ARE logged in, open the search URL in the
    profile browser, inspect a post card, and update the selectors below.

Relevance filter (is_hiring_post):
    Unlike the other sources we do NOT use is_relevant_job() here — post text is
    freeform prose, not a job title. A post is kept only if it mentions a user
    keyword stem AND contains a hiring signal.

Job dict schema (same shape as every other scraper):
    title       -> the post's opening line (the hook), truncated
    url         -> canonical post URL (used for dedup)
    description -> "{author} · LinkedIn hiring post"
    source      -> "LinkedIn Post"
    published   -> relative time string as shown on the post (e.g. "5h")
"""

import logging
import re
import time
from datetime import datetime
from urllib.parse import quote

from bs4 import BeautifulSoup

from browser import wait_for
from filters import is_hiring_post

log = logging.getLogger(__name__)

_MAX_SCROLLS = 4          # how many times to scroll to lazy-load more posts
_SCROLL_PAUSE = 2.0       # seconds to wait after each scroll
POSTS_WAIT_SECONDS = 15
CARD_SELECTOR = 'div[data-urn*="urn:li:activity"]'


class LinkedInLoginRequired(RuntimeError):
    """LinkedIn redirected to a login / authwall page: the user must log in once in the Chrome profile."""


def _post_within_48h(rel: str) -> bool:
    """
    Best-effort recency check for LinkedIn's compact relative timestamps.

    LinkedIn shows post age as "34m", "5h", "1d", "2w", "3mo", "1yr". The URL
    already filters to past-24h, so this is a safety net.
        minutes / hours -> always within 48h
        days            -> keep if <= 2
        weeks/months/yrs-> reject
        unparseable     -> keep (benefit of the doubt)
    """
    if not rel:
        return True
    r = rel.lower().strip()
    m = re.search(r"(\d+)\s*(mo|yr|y|w|d|h|hr|hour|m|min)", r)
    if not m:
        return True
    n, unit = int(m.group(1)), m.group(2)
    if unit in ("m", "min", "h", "hr", "hour"):
        return True
    if unit == "d":
        return n <= 2
    return False  # w, mo, yr, y


def _build_url(keyword: str) -> str:
    """Build a LinkedIn content-search URL for the given keyword phrase."""
    kw = quote(keyword)
    return (
        f"https://www.linkedin.com/search/results/content/?keywords={kw}"
        f"&datePosted=%22past-24h%22&sortBy=%22date_posted%22"
    )


def _is_logged_out(driver) -> bool:
    """True if LinkedIn bounced us to a login / authwall / checkpoint page."""
    try:
        cur = (driver.current_url or "").lower()
    except Exception:
        return True
    return any(x in cur for x in ("/login", "/authwall", "/checkpoint", "linkedin.com/uas"))


def _parse_posts_page(html: str, seen_urls: set, keyword: str, keywords: list) -> list:
    """Parse a LinkedIn content-search results page into job dicts."""
    soup = BeautifulSoup(html, "html.parser")
    result = []

    containers = soup.select('div[data-urn*="urn:li:activity"]')
    if not containers:
        containers = soup.select(".feed-shared-update-v2")

    for card in containers:
        try:
            urn = card.get("data-urn", "")
            if not urn or "activity" not in urn:
                nested = card.select_one('[data-urn*="urn:li:activity"]')
                urn = nested.get("data-urn", "") if nested else ""
            if not urn:
                continue

            url = f"https://www.linkedin.com/feed/update/{urn}/"
            if url in seen_urls:
                continue

            text_tag = card.select_one(
                ".update-components-text, .feed-shared-update-v2__description, "
                ".feed-shared-inline-show-more-text"
            )
            post_text = text_tag.get_text(" ", strip=True) if text_tag else ""
            if not is_hiring_post(post_text, keywords):
                continue

            author_tag = card.select_one(
                ".update-components-actor__title, .update-components-actor__name"
            )
            author = author_tag.get_text(" ", strip=True) if author_tag else "LinkedIn member"
            half = len(author) // 2
            if half and author[:half].strip() == author[half:].strip():
                author = author[:half].strip()

            time_tag = card.select_one(".update-components-actor__sub-description")
            rel = time_tag.get_text(" ", strip=True) if time_tag else ""
            rel = rel.split("•")[0].strip() if rel else ""
            if not _post_within_48h(rel):
                continue

            seen_urls.add(url)

            hook = " ".join(post_text.split())
            title = (hook[:90] + "…") if len(hook) > 90 else hook
            if not title:
                title = f"{author} is hiring"

            result.append({
                "title": title,
                "url": url,
                "description": f"{author} · LinkedIn hiring post",
                "source": "LinkedIn Post",
                "keyword": keyword,
                "location": "",
                "published": rel,
                "date_found": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            })
        except Exception:
            continue

    return result


def fetch_linkedin_posts(driver, args, seen_urls: set, keywords: list) -> list:
    """One LinkedIn content search. args = phrase, e.g. "hiring Java Developer". Raises LinkedInLoginRequired."""
    phrase = args
    url = _build_url(phrase)
    driver.get(url)
    if _is_logged_out(driver):
        raise LinkedInLoginRequired(driver.current_url)
    loaded = wait_for(driver, CARD_SELECTOR, POSTS_WAIT_SECONDS)
    # Second check: catches a redirect that happens after the page starts loading.
    if _is_logged_out(driver):
        raise LinkedInLoginRequired(driver.current_url)

    for _ in range(_MAX_SCROLLS):
        driver.execute_script("window.scrollBy(0, document.body.scrollHeight);")
        time.sleep(_SCROLL_PAUSE)

    jobs = _parse_posts_page(driver.page_source, seen_urls, phrase, keywords)
    if not jobs and not loaded:
        log.warning("LinkedIn posts: no post cards after %ss at %s (page title: %r)", POSTS_WAIT_SECONDS, url, driver.title)
    return jobs
