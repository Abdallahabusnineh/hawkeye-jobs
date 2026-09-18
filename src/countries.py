"""Fetch world countries from a public API when the user runs hawkeye-jobs."""

from __future__ import annotations

import json
from pathlib import Path

API_URL = "https://restcountries.com/v3.1/all?fields=name,cca2"
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = ROOT / "countries_cache.json"

REMOTE = {"id": "remote", "label": "Remote"}


class CountryFetchError(RuntimeError):
    """Raised when the live country API fails and there is no cached list."""


def parse_countries(payload) -> list:
    """Turn REST Countries JSON into [{id, label}, ...] sorted by name."""
    countries = []
    seen = set()
    for row in payload or []:
        name = ((row.get("name") or {}).get("common") or "").strip()
        cca2 = (row.get("cca2") or "").strip().lower()
        if not name or not cca2 or cca2 in seen:
            continue
        seen.add(cca2)
        countries.append({"id": cca2, "label": name})
    countries.sort(key=lambda item: item["label"].lower())
    return countries


def _with_remote(countries: list) -> list:
    rest = [c for c in countries if c.get("id") != "remote"]
    return [dict(REMOTE), *[dict(c) for c in rest]]


def _save_cache(path: Path, countries: list) -> None:
    path.write_text(json.dumps(countries, indent=2) + "\n", encoding="utf-8")


def _load_cache(path: Path) -> list:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    cleaned = []
    for row in data:
        if not isinstance(row, dict):
            continue
        loc_id = str(row.get("id") or "").strip().lower()
        label = str(row.get("label") or "").strip()
        if loc_id and label and loc_id != "remote":
            cleaned.append({"id": loc_id, "label": label})
    return cleaned


def _http_fetch(url: str):
    import requests

    response = requests.get(
        url,
        timeout=20,
        headers={"User-Agent": "hawkeye-jobs/1.0"},
    )
    response.raise_for_status()
    return response.json()


def load_countries(fetcher=None, cache_path: Path | None = None) -> list:
    """
    Return Remote + world countries from REST Countries.

    Every run calls the API. A local cache is only used if that request fails.
    """
    cache_path = cache_path or DEFAULT_CACHE
    fetch = fetcher or _http_fetch
    try:
        payload = fetch(API_URL)
        countries = parse_countries(payload)
        if countries:
            try:
                _save_cache(cache_path, countries)
            except OSError:
                pass
            return _with_remote(countries)
    except Exception:
        pass

    cached = _load_cache(cache_path)
    if cached:
        return _with_remote(cached)
    raise CountryFetchError(
        "Could not load countries from restcountries.com. Check your internet and try again."
    )
