"""Interactive search settings: prompt once, reuse from config.json next time."""

import json
from pathlib import Path

from catalog import LOCATION_ORDER, LOCATIONS, SOURCE_ORDER, SOURCES

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config.json"


def parse_selection(raw: str, count: int) -> list:
    """Parse 'all', '1 3', or '2,3' into 0-based indexes."""
    text = (raw or "").strip().lower()
    if text == "all":
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


def load_config(path: Path = DEFAULT_CONFIG_PATH):
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
    sources = data.get("sources") or []
    if not keywords or not locations or not sources:
        return None
    return {
        "keywords": list(keywords),
        "locations": list(locations),
        "sources": list(sources),
    }


def save_config(data: dict, path: Path = DEFAULT_CONFIG_PATH) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _format_saved(data: dict) -> str:
    labels = [LOCATIONS[i]["label"] for i in data["locations"] if i in LOCATIONS]
    source_labels = [SOURCES[i] for i in data["sources"] if i in SOURCES]
    return (
        f"  Keywords: {', '.join(data['keywords'])}\n"
        f"  Locations: {', '.join(labels)}\n"
        f"  Sources: {', '.join(source_labels)}"
    )


def _ask(prompt: str) -> str:
    return input(prompt).strip()


def _prompt_keywords() -> list:
    while True:
        raw = _ask("Search keywords (comma-separated, e.g. Python Developer, Django):\n> ")
        keywords = parse_keywords(raw)
        if keywords:
            return keywords
        print("Enter at least one keyword.")


def _prompt_from_list(title: str, items: list) -> list:
    print(f"\n{title}")
    for i, label in enumerate(items, start=1):
        print(f"  {i}) {label}")
    while True:
        raw = _ask("Choose numbers, or 'all':\n> ")
        try:
            indexes = parse_selection(raw, len(items))
        except ValueError as exc:
            print(f"  {exc}")
            continue
        return indexes


def prompt_new_settings() -> dict:
    keywords = _prompt_keywords()
    loc_idx = _prompt_from_list(
        "Locations",
        [LOCATIONS[key]["label"] for key in LOCATION_ORDER],
    )
    src_idx = _prompt_from_list(
        "Sources",
        [SOURCES[key] for key in SOURCE_ORDER],
    )
    return {
        "keywords": keywords,
        "locations": [LOCATION_ORDER[i] for i in loc_idx],
        "sources": [SOURCE_ORDER[i] for i in src_idx],
    }


def collect_settings(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    """Return settings from a Y/n reuse prompt, or ask and save a new config."""
    saved = load_config(path)
    if saved:
        print("Saved search settings:\n" + _format_saved(saved))
        answer = _ask("Use these? [Y/n] ").lower()
        if answer in ("", "y", "yes"):
            return saved

    data = prompt_new_settings()
    save_config(data, path)
    print(f"\nSaved to {path.name}. Next run can reuse these.")
    return data
