"""Fetch world countries from a public API when the user runs hawkeye-jobs."""

from __future__ import annotations

import json
from pathlib import Path

API_URLS = [
    "https://api.first.org/data/v1/countries?limit=300",
    "https://raw.githubusercontent.com/mledoze/countries/master/countries.json",
    "https://countriesnow.space/api/v0.1/countries/iso",
]
API_URL = API_URLS[0]
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = ROOT / "countries_cache.json"

REMOTE = {"id": "remote", "label": "Remote"}


class CountryFetchError(RuntimeError):
    """Raised when the live country API fails and there is no cached list."""


def _rows_from_payload(payload) -> list:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    if payload.get("success") is False:
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        rows = []
        for code, info in data.items():
            if isinstance(info, dict):
                rows.append({
                    "name": info.get("country") or info.get("name") or "",
                    "cca2": info.get("cca2") or code,
                })
            elif isinstance(info, str):
                rows.append({"name": info, "cca2": code})
        return rows
    return []


def _country_from_row(row) -> dict | None:
    if not isinstance(row, dict):
        return None
    name = ""
    nested = row.get("name")
    if isinstance(nested, dict):
        name = str(nested.get("common") or nested.get("official") or "").strip()
    elif isinstance(nested, str):
        name = nested.strip()
    if not name:
        name = str(row.get("country") or row.get("label") or "").strip()
    cca2 = str(
        row.get("cca2") or row.get("Iso2") or row.get("iso2") or row.get("code") or ""
    ).strip().lower()
    if not name or len(cca2) != 2:
        return None
    return {"id": cca2, "label": name}


def parse_countries(payload) -> list:
    """Turn a public country-API payload into [{id, label}, ...] sorted by name."""
    countries = []
    seen = set()
    for row in _rows_from_payload(payload):
        item = _country_from_row(row)
        if not item or item["id"] in seen:
            continue
        seen.add(item["id"])
        countries.append(item)
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
    payload = response.json()
    if isinstance(payload, dict) and payload.get("success") is False:
        raise RuntimeError("country API rejected the request")
    return payload


def load_countries(fetcher=None, cache_path: Path | None = None) -> list:
    """
    Return Remote + world countries from a public API.

    Every `bash run.sh` run calls the API. A local cache is only used if every
    live request fails.
    """
    cache_path = cache_path or DEFAULT_CACHE
    fetch = fetcher or _http_fetch
    for url in API_URLS:
        try:
            payload = fetch(url)
            countries = parse_countries(payload)
            if countries:
                try:
                    _save_cache(cache_path, countries)
                except OSError:
                    pass
                return _with_remote(countries)
        except Exception:
            continue

    cached = _load_cache(cache_path)
    if cached:
        return _with_remote(cached)
    raise CountryFetchError(
        "Could not load countries from the public API. Check your internet and try again."
    )
