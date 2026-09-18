"""Title and post relevance filters driven by the user's search keywords."""

import re

_STOPWORDS = {
    "developer", "engineer", "engineering", "dev", "development",
    "jobs", "job", "senior", "junior", "mid", "the", "and", "for",
    "with", "role", "position", "specialist",
}

_HIRING_SIGNALS = [
    "hiring", "we're hiring", "were hiring", "we are hiring",
    "looking for", "wanted", "join our team", "join us",
    "open position", "open role", "opening", "vacancy", "vacancies",
    "now hiring", "apply now", "send your cv", "send your resume", "dm me",
]


def keyword_stems(keywords: list) -> list:
    """Distinct meaningful tokens from user keywords, skipping generic role words."""
    stems = []
    seen = set()
    for raw in keywords or []:
        kw = str(raw).strip().lower()
        if not kw:
            continue
        words = [w for w in re.split(r"\W+", kw) if w]
        cores = [w for w in words if w not in _STOPWORDS and len(w) >= 2]
        chosen = cores if cores else [kw]
        for item in chosen:
            if item not in seen:
                seen.add(item)
                stems.append(item)
    return stems


def is_relevant_job(title: str, keywords: list) -> bool:
    """True if the title contains a user keyword stem as a whole word."""
    if not title:
        return False
    if not keywords:
        return True
    text = title.lower()
    for stem in keyword_stems(keywords):
        if re.search(r"\b" + re.escape(stem) + r"\b", text):
            return True
    return False


def is_hiring_post(text: str, keywords: list) -> bool:
    """True if post text looks like a hiring post and mentions a user keyword."""
    body = (text or "").lower()
    if not any(sig in body for sig in _HIRING_SIGNALS):
        return False
    stems = keyword_stems(keywords)
    if not stems:
        return True
    return any(re.search(r"\b" + re.escape(stem) + r"\b", body) for stem in stems)
