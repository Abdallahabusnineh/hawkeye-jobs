"""World country list, built offline from Babel's bundled CLDR data (no network)."""

from __future__ import annotations

import re

REMOTE = {"id": "remote", "label": "Remote"}

_REMOTE_LABELS = {"ar": "عن بُعد", "en": "Remote"}

# Two-letter CLDR codes that are not places a job can be in: unions, pseudo-locales,
# unknown region, and sub-areas whose jobs are listed under their country.
_NOT_A_COUNTRY = {"ZZ", "EU", "EZ", "UN", "QO", "XA", "XB",
                  "AC", "CP", "CQ", "DG", "EA", "IC", "TA"}

# Names we show instead of CLDR's, per language.
_LABEL_OVERRIDES = {"PS": {"en": "Palestine", "ar": "فلسطين"}}


def pretty_country_name(name: str) -> str:
    """Drop UN-style suffixes like '(the)' from official country names (older saved labels)."""
    text = str(name or "").strip()
    return re.sub(r"\s*\(the\)\s*$", "", text, flags=re.IGNORECASE).strip()


def _territories(language: str) -> dict:
    try:
        from babel import Locale
        return Locale.parse(language).territories
    except Exception:
        return {}


def _label(code: str, language: str, territories: dict, fallback: str) -> str:
    override = _LABEL_OVERRIDES.get(code, {}).get(language)
    if override:
        return override
    return territories.get(code, fallback)


def load_countries() -> list:
    """Remote + every country in English, sorted by name. Offline; never fails."""
    territories = _territories("en")
    rows = [
        {"id": code.lower(), "label": _label(code, "en", territories, code)}
        for code in territories
        if len(code) == 2 and code.isalpha() and code not in _NOT_A_COUNTRY
    ]
    if not rows:
        # Babel data missing (should not happen): fall back to the countries we have site mappings for.
        from catalog import OVERRIDES
        rows = [{"id": cid, "label": spec["label"]} for cid, spec in OVERRIDES.items()]
    rows.sort(key=lambda c: c["label"].casefold())
    return [dict(REMOTE), *rows]


def localized_countries(language: str, base: list) -> list:
    """[{id, label}] in the UI language (Babel/CLDR, offline), Remote first, the rest sorted by label."""
    territories = _territories(language)
    rest = []
    for c in base:
        if c["id"] == "remote":
            continue
        code = c["id"].upper()
        label = c["label"]
        if code not in _NOT_A_COUNTRY:
            label = _label(code, language, territories, label)
        rest.append({"id": c["id"], "label": label})
    rest.sort(key=lambda c: c["label"].casefold())
    return [{"id": "remote", "label": _REMOTE_LABELS.get(language, "Remote")}] + rest
