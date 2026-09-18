"""Maps user-facing locations/sources to per-site search queries."""

LEGACY_IDS = {
    "jordan": "jo",
    "uae": "ae",
    "saudi": "sa",
    "kuwait": "kw",
    "qatar": "qa",
    "oman": "om",
    "bahrain": "bh",
    "uk": "gb",
    "germany": "de",
    "netherlands": "nl",
    "france": "fr",
    "usa": "us",
    "canada": "ca",
}

OVERRIDES = {
    "jo": {
        "label": "Jordan",
        "indeed": ("Jordan", "jordan"),
        "bayt": "jordan",
    },
    "ae": {
        "label": "United Arab Emirates",
        "indeed": ("United Arab Emirates", "united arab emirates"),
        "bayt": "uae",
        "naukrigulf": "uae",
        "google": "UAE",
    },
    "sa": {
        "label": "Saudi Arabia",
        "indeed": ("Saudi Arabia", "saudi arabia"),
        "bayt": "saudi-arabia",
        "naukrigulf": "saudi-arabia",
    },
    "kw": {
        "label": "Kuwait",
        "indeed": ("Kuwait", "kuwait"),
        "bayt": "kuwait",
        "naukrigulf": "kuwait",
    },
    "qa": {
        "label": "Qatar",
        "indeed": ("Qatar", "qatar"),
        "bayt": "qatar",
        "naukrigulf": "qatar",
    },
    "om": {
        "label": "Oman",
        "indeed": ("Oman", "oman"),
        "bayt": "oman",
    },
    "bh": {
        "label": "Bahrain",
        "indeed": ("Bahrain", "bahrain"),
        "bayt": "bahrain",
        "naukrigulf": "bahrain",
    },
    "gb": {
        "label": "United Kingdom",
        "indeed": ("United Kingdom", "uk"),
    },
    "de": {
        "label": "Germany",
        "indeed": ("Germany", "germany"),
    },
    "nl": {
        "label": "Netherlands",
        "indeed": ("Netherlands", "netherlands"),
    },
    "fr": {
        "label": "France",
        "indeed": ("France", "france"),
    },
    "us": {
        "label": "United States",
        "indeed": ("United States", "usa"),
    },
    "ca": {
        "label": "Canada",
        "indeed": ("Canada", "canada"),
    },
}

REMOTE_SPEC = {
    "label": "Remote",
    "linkedin": "Remote",
    "indeed": None,
    "bayt": None,
    "naukrigulf": None,
    "google": None,
}

SOURCES = {
    "linkedin": "LinkedIn jobs",
    "indeed": "Indeed",
    "bayt": "Bayt.com",
    "naukrigulf": "NaukriGulf",
    "google_jobs": "Google Jobs",
    "linkedin_posts": "LinkedIn hiring posts",
}

SOURCE_HINTS = {
    "linkedin": "worldwide",
    "indeed": "worldwide, except Remote",
    "bayt": "Jordan + Gulf only",
    "naukrigulf": "Gulf only",
    "google_jobs": "worldwide",
    "linkedin_posts": "global posts — LinkedIn login required",
}

SOURCE_ORDER = [
    "linkedin", "indeed", "bayt", "naukrigulf", "google_jobs", "linkedin_posts",
]

BROWSER_SOURCES = {"bayt", "naukrigulf", "google_jobs", "linkedin_posts"}


def source_choice_label(source_id: str) -> str:
    label = SOURCES[source_id]
    hint = SOURCE_HINTS.get(source_id)
    return f"{label}  ({hint})" if hint else label


def normalize_location_id(loc_id: str) -> str:
    return LEGACY_IDS.get((loc_id or "").strip().lower(), (loc_id or "").strip().lower())


def get_location(loc_id: str, label: str | None = None) -> dict | None:
    """Build per-site location fields. Unknown countries still work on LinkedIn/Google/Indeed."""
    loc_id = normalize_location_id(loc_id)
    if not loc_id:
        return None
    if loc_id == "remote":
        return dict(REMOTE_SPEC)
    over = OVERRIDES.get(loc_id, {})
    name = label or over.get("label") or loc_id.upper()
    return {
        "label": name,
        "linkedin": name,
        "indeed": over.get("indeed") or (name, name.lower()),
        "bayt": over.get("bayt"),
        "naukrigulf": over.get("naukrigulf"),
        "google": over.get("google", name),
    }


def _empty_searches() -> dict:
    return {key: [] for key in SOURCE_ORDER}


def _slug(keyword: str) -> str:
    return "-".join(keyword.strip().lower().split())


def expand_searches(keywords: list, location_ids: list, source_ids: list, country_labels=None) -> dict:
    """Build per-source query lists from the user's keywords, locations, and sources."""
    searches = _empty_searches()
    selected = set(source_ids)
    keywords = [kw.strip() for kw in keywords if str(kw).strip()]
    country_labels = country_labels or {}

    for loc_id in location_ids:
        nid = normalize_location_id(loc_id)
        loc = get_location(loc_id, country_labels.get(nid) or country_labels.get(loc_id))
        if not loc:
            continue
        for keyword in keywords:
            if "linkedin" in selected and loc.get("linkedin"):
                searches["linkedin"].append({
                    "term": keyword,
                    "location": loc["linkedin"],
                })
            if "indeed" in selected and loc.get("indeed"):
                city, country = loc["indeed"]
                searches["indeed"].append({
                    "term": keyword,
                    "location": city,
                    "country": country,
                })
            if "bayt" in selected and loc.get("bayt"):
                searches["bayt"].append((_slug(keyword), loc["bayt"]))
            if "naukrigulf" in selected and loc.get("naukrigulf"):
                searches["naukrigulf"].append((keyword.lower(), loc["naukrigulf"]))
            if "google_jobs" in selected:
                if nid == "remote":
                    searches["google_jobs"].append((f"{keyword} remote", ""))
                elif loc.get("google") is not None:
                    searches["google_jobs"].append((keyword, loc["google"]))

    if "linkedin_posts" in selected:
        for keyword in keywords:
            searches["linkedin_posts"].extend([
                f"hiring {keyword}",
                f"looking for {keyword}",
            ])

    return searches
