"""Interactive search settings: prompt once, reuse from config.json next time."""

import json
import sys
from pathlib import Path

from catalog import LOCATION_ORDER, LOCATIONS, SOURCE_ORDER, SOURCES, source_choice_label
from picker import color_question, pick_many, pick_one

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config.json"


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
        raw = _ask(color_question("Search keywords (comma-separated, e.g. Flutter Developer, Dart Developer):") + "\n> ")
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
        return indexes


def prompt_new_settings(interactive: bool = True) -> dict:
    keywords = _prompt_keywords()
    location_labels = [LOCATIONS[key]["label"] for key in LOCATION_ORDER]
    source_labels = [source_choice_label(key) for key in SOURCE_ORDER]
    if interactive:
        loc_idx = pick_many("Locations", location_labels)
        src_idx = pick_many("Which websites should I search?", source_labels)
    else:
        loc_idx = _prompt_from_list("Locations", location_labels)
        src_idx = _prompt_from_list("Which websites should I search?", source_labels)
    return {
        "keywords": keywords,
        "locations": [LOCATION_ORDER[i] for i in loc_idx],
        "sources": [SOURCE_ORDER[i] for i in src_idx],
    }


def collect_settings(path: Path = DEFAULT_CONFIG_PATH, interactive=None) -> dict:
    """Return settings from a reuse prompt, or ask and save a new config."""
    if interactive is None:
        interactive = can_use_picker()

    saved = load_config(path)
    if saved:
        print("Saved search settings:\n" + _format_saved(saved))
        if interactive:
            choice = pick_one("Use these saved settings?", ["Yes", "No"], default=0)
            if choice == 0:
                return saved
        else:
            answer = _ask("Use these? [Y/n] ").lower()
            if answer in ("", "y", "yes"):
                return saved

    data = prompt_new_settings(interactive=interactive)
    save_config(data, path)
    print(f"\nSaved to {path.name}. Next run can reuse these.")
    return data
