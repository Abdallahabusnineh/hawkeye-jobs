"""World country list, bundled with the app (src/data/countries.json; no network).

The list is the UN member states plus Palestine, Hong Kong and Macau, with English and Arabic
names, built from mledoze/countries by tools/update_countries.py (ODbL; see
src/data/COUNTRIES-LICENSE.md). Territories, uninhabited islands and disputed regions are not
listed; a location saved by an older version that is no longer listed is dropped quietly
(web/api.py).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "countries.json"
REMOTE = {"id": "remote", "label": "Remote"}
_REMOTE_LABELS = {"ar": "عن بُعد", "en": "Remote"}


def pretty_country_name(name: str) -> str:
    """Drop UN-style suffixes like '(the)' from official country names (older saved labels)."""
    text = str(name or "").strip()
    return re.sub(r"\s*\(the\)\s*$", "", text, flags=re.IGNORECASE).strip()


@lru_cache(maxsize=1)
def _rows() -> tuple:
    return tuple(json.loads(DATA.read_text(encoding="utf-8")))


def load_countries() -> list:
    """Remote + every listed country in English, sorted by name."""
    rows = [{"id": r["id"], "label": r["en"]} for r in _rows()]
    rows.sort(key=lambda c: c["label"].casefold())
    return [dict(REMOTE), *rows]


def localized_countries(language: str, base: list) -> list:
    """[{id, label}] in the UI language, Remote first, the rest sorted by label."""
    names = {r["id"]: r.get(language) or r["en"] for r in _rows()}
    rest = [{"id": c["id"], "label": names.get(c["id"], c["label"])} for c in base if c["id"] != "remote"]
    rest.sort(key=lambda c: c["label"].casefold())
    return [{"id": "remote", "label": _REMOTE_LABELS.get(language, "Remote")}] + rest
