"""Interactive search settings: prompt once, reuse from config.json next time."""

import json
import logging
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

from catalog import REMOVED_SOURCES, SOURCE_ORDER, SOURCES, get_location, normalize_location_id, source_choice_label
from countries import load_countries
from paths import config_path
from utils import DEFAULT_JOB_AGE_DAYS, JOB_AGE_CHOICES
from picker import color_question, color_selected, confirmed_line, pick_many, pick_one, pick_searchable

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
SEARCH_KEYS = ("keywords", "locations", "sources")
LANGUAGES = ("ar", "en")
THEMES = ("auto", "light", "dark")
AUTO_HOURS = (2, 4, 6, 12, 24)
USER_APP_KEYS = ("language", "theme", "auto_search", "job_age_days", "keep_running", "start_at_login",
                 "onboarding_done", "update_check")
_INTERNAL_APP_KEYS = ("last_update_check", "latest_version", "background_hint_shown")
APP_DEFAULTS = {
    "language": "ar",
    "theme": "auto",
    "auto_search": {"enabled": True, "hours": 6},
    "job_age_days": DEFAULT_JOB_AGE_DAYS,  # show jobs posted within this many days (utils.JOB_AGE_CHOICES)
    "keep_running": True,       # closing the window hides it; the app keeps searching from the tray
    "start_at_login": False,    # an OS login item starts the app hidden (background.Autostart)
    "onboarding_done": False,
    "update_check": True,
    "last_update_check": None,
    "latest_version": None,
    "background_hint_shown": None,  # when we first told the user the app keeps running after close
}
# One lock for every read-modify-write of config.json (API threads + the run worker both save).
_CONFIG_LOCK = threading.RLock()
MAX_KEYWORDS = 20
MAX_KEYWORD_LENGTH = 100


class SettingsError(ValueError):
    """Invalid settings. `errors` maps each bad field to a code: required / invalid / too_long / too_many / unknown."""

    def __init__(self, errors: dict):
        super().__init__(", ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def parse_selection(raw: str, count: int) -> list:
    """Parse 'all', '1 3', or '2,3' into 0-based indexes (non-TTY fallback)."""
    text = (raw or "").strip().lower()
    if text in ("all", "*", "0"):
        return list(range(count))
    text = text.replace(",", " ")
    indexes = []
    for part in text.split():
        try:
            number = int(part)
        except ValueError as exc:
            raise ValueError(f"Not a number: {part}") from exc
        if number < 1 or number > count:
            raise ValueError(f"Choice {number} is out of range")
        idx = number - 1
        if idx not in indexes:
            indexes.append(idx)
    if not indexes:
        raise ValueError("Select at least one option")
    return indexes


def parse_keywords(raw: str) -> list:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def load_config(path=None):
    path = Path(path) if path else config_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    keywords = data.get("keywords") or []
    locations = data.get("locations") or []
    # A source removed in a later version (REMOVED_SOURCES) is dropped, so an old search still runs.
    sources = [s for s in (data.get("sources") or []) if s not in REMOVED_SOURCES]
    if not keywords or not locations or not sources:
        return None
    return {
        "keywords": list(keywords),
        "locations": list(locations),
        "sources": list(sources),
    }


def _read_config_dict(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_json_atomic(path: Path, data: dict) -> None:
    """Write to a temp file in the same folder, then rename — a crash never leaves half a file."""
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_config(data: dict, path=None) -> None:
    """Write keywords/locations/sources, keeping any other keys already in the file (e.g. the app's settings)."""
    path = Path(path) if path else config_path()
    with _CONFIG_LOCK:
        existing = _read_config_dict(path)
        for key in SEARCH_KEYS:
            existing[key] = list(data[key])
        _write_json_atomic(path, existing)


def _valid_app_value(key: str, value) -> bool:
    if key == "language":
        return value in LANGUAGES
    if key == "theme":
        return value in THEMES
    if key == "auto_search":
        return (isinstance(value, dict) and set(value) == {"enabled", "hours"}
                and isinstance(value["enabled"], bool) and value["hours"] in AUTO_HOURS)
    if key == "job_age_days":
        return value in JOB_AGE_CHOICES and not isinstance(value, bool)
    if key in ("onboarding_done", "update_check", "keep_running", "start_at_login"):
        return isinstance(value, bool)
    if key in _INTERNAL_APP_KEYS:
        return value is None or isinstance(value, str)
    return False


def load_app_settings(path=None) -> dict:
    """The app's settings with defaults filled in; invalid stored values fall back to defaults."""
    path = Path(path) if path else config_path()
    stored = _read_config_dict(path).get("app")
    stored = stored if isinstance(stored, dict) else {}
    result = json.loads(json.dumps(APP_DEFAULTS))  # deep copy
    for key in APP_DEFAULTS:
        if key in stored and _valid_app_value(key, stored[key]):
            result[key] = stored[key]
    return result


def save_app_settings(patch: dict, path=None) -> dict:
    """Validate `patch`, merge it into config.json → "app", write atomically, return the full settings."""
    path = Path(path) if path else config_path()
    errors = {}
    for key, value in patch.items():
        if key not in APP_DEFAULTS:
            errors[key] = "unknown"
        elif not _valid_app_value(key, value):
            errors[key] = "invalid"
    if errors:
        raise SettingsError(errors)
    with _CONFIG_LOCK:
        data = _read_config_dict(path)
        app = load_app_settings(path)
        app.update(patch)
        data["app"] = app
        _write_json_atomic(path, data)
    return app


def validate_search_settings(data: dict, allowed_locations: set) -> dict:
    """Clean and validate search settings coming from the UI. Raises SettingsError with every bad field."""
    errors = {}
    keywords = data.get("keywords")
    cleaned_keywords = []
    if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
        errors["keywords"] = "invalid"
    else:
        for keyword in (k.strip() for k in keywords):
            if keyword and keyword not in cleaned_keywords:
                cleaned_keywords.append(keyword)
        if not cleaned_keywords:
            errors["keywords"] = "required"
        elif any(len(k) > MAX_KEYWORD_LENGTH for k in cleaned_keywords):
            errors["keywords"] = "too_long"
        elif len(cleaned_keywords) > MAX_KEYWORDS:
            errors["keywords"] = "too_many"

    locations = data.get("locations")
    cleaned_locations = []
    if not isinstance(locations, list) or not all(isinstance(x, str) for x in locations):
        errors["locations"] = "invalid"
    else:
        for loc in (normalize_location_id(x) for x in locations):
            if loc and loc not in cleaned_locations:
                cleaned_locations.append(loc)
        if not cleaned_locations:
            errors["locations"] = "required"
        elif any(loc not in allowed_locations for loc in cleaned_locations):
            errors["locations"] = "invalid"

    sources = data.get("sources")
    cleaned_sources = []
    if not isinstance(sources, list) or not all(isinstance(x, str) for x in sources):
        errors["sources"] = "invalid"
    else:
        for source in sources:
            if source not in cleaned_sources:
                cleaned_sources.append(source)
        if not cleaned_sources:
            errors["sources"] = "required"
        elif any(s not in SOURCE_ORDER for s in cleaned_sources):
            errors["sources"] = "invalid"

    if errors:
        raise SettingsError(errors)
    return {"keywords": cleaned_keywords, "locations": cleaned_locations, "sources": cleaned_sources}


def migrate_legacy_config(root: Path = ROOT, target=None) -> bool:
    """Copy <repo>/config.json into the data directory once. The original stays where it is."""
    target = Path(target) if target else config_path()
    legacy = Path(root) / "config.json"
    if target.exists() or not legacy.exists():
        return False
    shutil.copy2(legacy, target)
    log.info("Copied legacy config %s to %s", legacy, target)
    return True


def _format_saved(data: dict, country_labels=None) -> str:
    country_labels = country_labels or {}
    labels = []
    for loc_id in data["locations"]:
        nid = normalize_location_id(loc_id)
        loc = get_location(loc_id, country_labels.get(nid) or country_labels.get(loc_id))
        labels.append(loc["label"] if loc else loc_id)
    source_labels = [SOURCES[i] for i in data["sources"] if i in SOURCES]
    return (
        f"  Keywords: {', '.join(data['keywords'])}\n"
        f"  Locations: {', '.join(labels)}\n"
        f"  Websites: {', '.join(source_labels)}"
    )


def _ask(prompt: str) -> str:
    return input(prompt).strip()


def can_use_picker() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def _prompt_keywords() -> list:
    while True:
        raw = _ask(color_question("Search keywords (comma-separated, e.g. Flutter Developer, Mobile Developer):") + "\n> ")
        keywords = parse_keywords(raw)
        if keywords:
            return keywords
        print("Enter at least one keyword.")


def _prompt_from_list(title: str, items: list) -> list:
    print(f"\n{color_question(title)}")
    print("  0) All")
    for i, label in enumerate(items, start=1):
        print(f"  {i}) {label}")
    while True:
        raw = _ask("Type 0 or all — or numbers like 1 2 5:\n> ")
        try:
            indexes = parse_selection(raw, len(items))
        except ValueError as exc:
            print(f"  {exc}")
            continue
        print(confirmed_line([items[i] for i in indexes]))
        return indexes


def _resolve_countries(countries) -> list:
    if countries is not None:
        return list(countries)
    return load_countries()


def confirm_choices_question(kind: str, names: list, limit: int = 8) -> str:
    labels = [str(name) for name in names]
    extra = ""
    if len(labels) > limit:
        extra = f" (+{len(labels) - limit} more)"
        labels = labels[:limit]
    return f"Confirm these {kind}: {', '.join(labels)}{extra}?"


def confirm_picker_title(kind: str) -> str:
    return f"Are these {kind} correct?"


def short_choice_name(name: str) -> str:
    return str(name).split("  (")[0]


def confirm_choices(kind: str, names: list, interactive: bool) -> bool:
    print()
    print(color_question(confirm_choices_question(kind, names)))
    for name in names:
        print(color_selected(f"  • {name}"))
    title = confirm_picker_title(kind)
    if interactive:
        return pick_one(title, ["Yes, continue", "No, choose again"], default=0) == 0
    answer = _ask(color_question(title) + " [Y/n]\n> ").lower()
    return answer in ("", "y", "yes")


def _pick_until_confirmed(kind: str, items: list, pick, interactive: bool) -> list:
    while True:
        indexes = pick()
        names = [short_choice_name(items[i]) for i in indexes]
        if confirm_choices(kind, names, interactive):
            return indexes


def prompt_new_settings(interactive: bool = True, countries=None) -> dict:
    keywords = _prompt_keywords()
    countries = _resolve_countries(countries)
    location_labels = [c["label"] for c in countries]
    source_labels = [source_choice_label(key) for key in SOURCE_ORDER]
    loc_pick = (
        (lambda: pick_searchable("Search and select countries", location_labels))
        if interactive
        else (lambda: _prompt_from_list("Search and select countries", location_labels))
    )
    src_pick = (
        (lambda: pick_many("Which websites should I search?", source_labels))
        if interactive
        else (lambda: _prompt_from_list("Which websites should I search?", source_labels))
    )
    loc_idx = _pick_until_confirmed("countries", location_labels, loc_pick, interactive)
    src_idx = _pick_until_confirmed("websites", source_labels, src_pick, interactive)
    return {
        "keywords": keywords,
        "locations": [countries[i]["id"] for i in loc_idx],
        "sources": [SOURCE_ORDER[i] for i in src_idx],
    }


def collect_settings(path=None, interactive=None, countries=None) -> dict:
    """Return settings from a reuse prompt, or ask and save a new config."""
    path = Path(path) if path else config_path()
    if interactive is None:
        interactive = can_use_picker()

    saved = load_config(path)
    if saved:
        resolved = _resolve_countries(countries)
        labels_map = {c["id"]: c["label"] for c in resolved}
        print("Saved search settings:\n" + _format_saved(saved, labels_map))
        if interactive:
            choice = pick_one("Use these saved settings?", ["Yes", "No"], default=0)
            if choice == 0:
                return saved
        else:
            answer = _ask("Use these? [Y/n] ").lower()
            if answer in ("", "y", "yes"):
                return saved
        countries = resolved

    data = prompt_new_settings(interactive=interactive, countries=countries)
    save_config(data, path)
    print(f"\nSaved to {path.name}. Next run can reuse these.")
    return data
