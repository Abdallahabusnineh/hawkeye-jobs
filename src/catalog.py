"""Maps user-facing locations/sources to per-site search queries."""

LOCATIONS = {
    "jordan": {
        "label": "Jordan",
        "linkedin": "Jordan",
        "indeed": ("Jordan", "jordan"),
        "bayt": "jordan",
        "naukrigulf": None,
        "google": "Jordan",
    },
    "uae": {
        "label": "United Arab Emirates",
        "linkedin": "United Arab Emirates",
        "indeed": ("United Arab Emirates", "united arab emirates"),
        "bayt": "uae",
        "naukrigulf": "uae",
        "google": "UAE",
    },
    "saudi": {
        "label": "Saudi Arabia",
        "linkedin": "Saudi Arabia",
        "indeed": ("Saudi Arabia", "saudi arabia"),
        "bayt": "saudi-arabia",
        "naukrigulf": "saudi-arabia",
        "google": "Saudi Arabia",
    },
    "kuwait": {
        "label": "Kuwait",
        "linkedin": "Kuwait",
        "indeed": ("Kuwait", "kuwait"),
        "bayt": "kuwait",
        "naukrigulf": "kuwait",
        "google": "Kuwait",
    },
    "qatar": {
        "label": "Qatar",
        "linkedin": "Qatar",
        "indeed": ("Qatar", "qatar"),
        "bayt": "qatar",
        "naukrigulf": "qatar",
        "google": "Qatar",
    },
    "oman": {
        "label": "Oman",
        "linkedin": "Oman",
        "indeed": ("Oman", "oman"),
        "bayt": "oman",
        "naukrigulf": None,
        "google": "Oman",
    },
    "bahrain": {
        "label": "Bahrain",
        "linkedin": "Bahrain",
        "indeed": ("Bahrain", "bahrain"),
        "bayt": "bahrain",
        "naukrigulf": "bahrain",
        "google": "Bahrain",
    },
    "uk": {
        "label": "United Kingdom",
        "linkedin": "United Kingdom",
        "indeed": ("United Kingdom", "uk"),
        "bayt": None,
        "naukrigulf": None,
        "google": "United Kingdom",
    },
    "germany": {
        "label": "Germany",
        "linkedin": "Germany",
        "indeed": ("Germany", "germany"),
        "bayt": None,
        "naukrigulf": None,
        "google": "Germany",
    },
    "netherlands": {
        "label": "Netherlands",
        "linkedin": "Netherlands",
        "indeed": ("Netherlands", "netherlands"),
        "bayt": None,
        "naukrigulf": None,
        "google": "Netherlands",
    },
    "france": {
        "label": "France",
        "linkedin": "France",
        "indeed": ("France", "france"),
        "bayt": None,
        "naukrigulf": None,
        "google": "France",
    },
    "usa": {
        "label": "United States",
        "linkedin": "United States",
        "indeed": ("United States", "usa"),
        "bayt": None,
        "naukrigulf": None,
        "google": "United States",
    },
    "canada": {
        "label": "Canada",
        "linkedin": "Canada",
        "indeed": ("Canada", "canada"),
        "bayt": None,
        "naukrigulf": None,
        "google": "Canada",
    },
    "remote": {
        "label": "Remote",
        "linkedin": "Remote",
        "indeed": None,
        "bayt": None,
        "naukrigulf": None,
        "google": None,
    },
}

LOCATION_ORDER = [
    "jordan", "uae", "saudi", "kuwait", "qatar", "oman", "bahrain",
    "uk", "germany", "netherlands", "france", "usa", "canada", "remote",
]

SOURCES = {
    "linkedin": "LinkedIn jobs",
    "indeed": "Indeed",
    "bayt": "Bayt.com",
    "naukrigulf": "NaukriGulf",
    "google_jobs": "Google Jobs",
    "linkedin_posts": "LinkedIn hiring posts",
}

SOURCE_ORDER = [
    "linkedin", "indeed", "bayt", "naukrigulf", "google_jobs", "linkedin_posts",
]

BROWSER_SOURCES = {"bayt", "naukrigulf", "google_jobs", "linkedin_posts"}


def _empty_searches() -> dict:
    return {key: [] for key in SOURCE_ORDER}


def _slug(keyword: str) -> str:
    return "-".join(keyword.strip().lower().split())


def expand_searches(keywords: list, location_ids: list, source_ids: list) -> dict:
    """Build per-source query lists from the user's keywords, locations, and sources."""
    searches = _empty_searches()
    selected = set(source_ids)
    keywords = [kw.strip() for kw in keywords if str(kw).strip()]

    for loc_id in location_ids:
        loc = LOCATIONS.get(loc_id)
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
                if loc_id == "remote":
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
