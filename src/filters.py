"""Title relevance filter driven by the user's search keywords."""

import re

_STOPWORDS = {
    "developer", "engineer", "engineering", "dev", "development",
    "jobs", "job", "senior", "junior", "mid", "the", "and", "for",
    "with", "role", "position", "specialist",
}

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
