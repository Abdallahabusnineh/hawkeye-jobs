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
        &datePosted=%22past-week%22&sortBy=%22date_posted%22
    - datePosted=past-week keeps posts to the last 7 days; cross-run
      dedup in store.py prevents re-emailing the same post.
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
import hashlib
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

from bs4 import BeautifulSoup

from filters import is_hiring_post

log = logging.getLogger(__name__)

_MAX_SCROLLS = 4          # how many times to scroll to lazy-load more posts
_SCROLL_PAUSE = 2.0       # seconds to wait after each scroll
POSTS_WAIT_SECONDS = 15



class LinkedInLoginRequired(RuntimeError):
    """LinkedIn redirected to a login / authwall page: the user must log in once in the Chrome profile."""


def _post_is_recent(rel: str) -> bool:
    """
    Best-effort recency check for LinkedIn's compact relative timestamps.

    LinkedIn shows post age as "34m", "5h", "1d", "2w", "3mo", "1yr". The URL
    already filters to past-week, so this is a safety net.
        minutes / hours -> always recent
        days            -> keep if <= 7
        1w              -> keep; 2w or more -> reject
        months/yrs      -> reject
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
        return n <= 7
    if unit == "w":
        return n <= 1
    return False  # mo, yr, y


def _build_url(keyword: str) -> str:
    """Build a LinkedIn content-search URL for the given keyword phrase."""
    kw = quote(keyword)
    return (
        f"https://www.linkedin.com/search/results/content/?keywords={kw}"
        f"&datePosted=%22past-week%22&sortBy=%22date_posted%22"
    )


def _is_logged_out(driver) -> bool:
    """True if LinkedIn bounced us to a login / authwall / checkpoint page."""
    try:
        cur = (driver.current_url or "").lower()
    except Exception:
        return True
    return any(x in cur for x in ("/login", "/authwall", "/checkpoint", "linkedin.com/uas"))


_REL_TIME = re.compile(r"^(now|\d+\s*(?:s|m|min|h|hr|d|w|mo|yr|y))$", re.IGNORECASE)


def _post_url(card, post_text: str) -> str:
    """The new markup has no link to the post itself. Link to the author's recent activity (the post
    is near the top, being recent) and add a fragment fingerprinting the post, so each post stays a
    distinct job in the store (clean_url keeps fragments)."""
    profile = ""
    for a in card.select("a[href]"):
        href = a.get("href", "").split("?")[0]
        if "linkedin.com/in/" in href or "linkedin.com/company/" in href:
            profile = href.rstrip("/") + "/"
            break
    fingerprint = hashlib.sha1(f"{profile}|{' '.join(post_text.split())[:500]}".encode("utf-8")).hexdigest()[:16]
    if "/company/" in profile:
        base = profile + "posts/"
    elif profile:
        base = profile + "recent-activity/all/"
    else:
        words = " ".join(post_text.split()[:8])
        base = f"https://www.linkedin.com/search/results/content/?keywords={quote(words)}"
    return f"{base}#hawkeye-{fingerprint}"


# Every post in LinkedIn's 2026 results starts with a visually hidden heading "Feed post" (for screen
# readers). From the second search in a session LinkedIn drops the data-view-name attributes and
# scrambles class names, but the heading stays — so posts are found by it. Inside a post the visible
# strings come in a fixed order: heading, author, [• degree], [headline], time, Follow/Connect, text.
_POST_HEADING = re.compile(r"^\s*Feed post\s*$")
_ACTION_WORDS = {"follow", "connect", "subscribe", "+ follow", "following"}
_FOOTER_WORDS = {"like", "comment", "repost", "send", "celebrate", "support", "love", "insightful", "funny"}


def _post_containers(soup) -> list:
    """The element wrapping each post: climb from its heading while the parent holds no other post."""
    containers = []
    for heading in soup.find_all(string=_POST_HEADING):
        el = heading.parent
        while el.parent is not None and len(el.parent.find_all(string=_POST_HEADING)) == 1:
            el = el.parent
        containers.append(el)
    return containers


def _split_post(card):
    """(author, relative time, post text) from a post's strings."""
    strings = [s for s in card.stripped_strings if not _POST_HEADING.match(s)]
    t = next((i for i, s in enumerate(strings) if _REL_TIME.match(s.strip())), None)
    author = strings[0] if strings else ""
    if t is None:
        return author, "", " ".join(strings[1:])
    body = strings[t + 1:]
    while body and body[0].strip().lower() in _ACTION_WORDS:
        body = body[1:]
    for i, s in enumerate(body):
        if s.strip().lower() in _FOOTER_WORDS:
            body = body[:i]
            break
    return author, strings[t].strip(), " ".join(body)


def _parse_new_markup(soup, seen_urls: set, keyword: str, keywords: list) -> list:
    """LinkedIn's 2026 search results (with or without data-view-name attributes)."""
    result = []
    for card in _post_containers(soup):
        try:
            author, rel, post_text = _split_post(card)
            commentary = card.select('[data-view-name="feed-commentary"]')
            if commentary:  # first search of a session: use the precise text node
                post_text = " ".join(t.get_text(" ", strip=True) for t in commentary)
            author = re.split(r"\s+•", author)[0].strip() or "LinkedIn member"
            if not rel:
                continue  # every post shows its age; without one this is a share's inner block, not a post
            if not is_hiring_post(post_text, keywords):
                continue
            if not _post_is_recent(rel):
                continue
            url = _post_url(card, post_text)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            hook = " ".join(post_text.split())
            title = (hook[:90] + "…") if len(hook) > 90 else hook
            result.append({
                "title": title or f"{author} is hiring",
                "url": url,
                "description": f"{author} · LinkedIn hiring post",
                "source": "LinkedIn Post",
                "keyword": keyword,
                "location": "",
                "published": rel,
                "date_found": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            })
        except Exception:
            continue
    return result


def _parse_posts_page(html: str, seen_urls: set, keyword: str, keywords: list) -> list:
    """Parse a LinkedIn content-search results page into job dicts (current or older markup)."""
    soup = BeautifulSoup(html, "html.parser")
    if soup.find(string=_POST_HEADING) is not None:
        return _parse_new_markup(soup, seen_urls, keyword, keywords)
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
            if not _post_is_recent(rel):
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
                "date_found": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            })
        except Exception:
            continue

    return result


def _wait_for_posts(driver, timeout: float) -> bool:
    """Wait for post headings (current markup, with or without attributes) or the older cards."""
    probe = ("return document.body !== null && (document.body.innerText.indexOf('Feed post') !== -1 || "
             "document.querySelector('div[data-urn*=\"urn:li:activity\"]') !== null)")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if driver.execute_script(probe):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def fetch_linkedin_posts(driver, args, seen_urls: set, keywords: list) -> list:
    """One LinkedIn content search. args = phrase, e.g. "hiring Java Developer". Raises LinkedInLoginRequired."""
    phrase = args
    url = _build_url(phrase)
    driver.get(url)
    if _is_logged_out(driver):
        raise LinkedInLoginRequired(driver.current_url)
    loaded = _wait_for_posts(driver, POSTS_WAIT_SECONDS)
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
