"""Rebuild src/data/countries.json from mledoze/countries (run by hand; the app never downloads it).

    python3 tools/update_countries.py

Which places: the UN member states plus Palestine (observer state), and Hong Kong and Macau
(separate job markets). Not listed: other territories, uninhabited islands and disputed regions.
Names: mledoze's common English and Arabic names, corrected by NAME_FIXES (spelling mistakes and
ambiguous names found in review). Data © mledoze/countries contributors, ODbL 1.0
(src/data/COUNTRIES-LICENSE.md).
"""

import json
import urllib.request
from pathlib import Path

COMMIT = "c2ac0049c14edcf2436c7aa1b2493222a020b462"  # pinned: update deliberately, then review the diff
SOURCE = f"https://raw.githubusercontent.com/mledoze/countries/{COMMIT}/countries.json"
OUT = Path(__file__).resolve().parent.parent / "src" / "data" / "countries.json"

EXTRA = {"PS", "HK", "MO"}
NAME_FIXES = {
    "MX": {"ar": "المكسيك"},
    "RS": {"ar": "صربيا"},
    "UZ": {"ar": "أوزبكستان"},
    "ME": {"ar": "الجبل الأسود"},
    "MD": {"ar": "مولدوفا"},
    "CD": {"ar": "جمهورية الكونغو الديمقراطية", "en": "DR Congo"},
    "CG": {"ar": "جمهورية الكونغو", "en": "Republic of the Congo"},
    "AO": {"ar": "أنغولا"},
    "TH": {"ar": "تايلاند"},
    "CV": {"ar": "الرأس الأخضر"},
    "VA": {"ar": "الفاتيكان", "en": "Vatican City"},
}


def main() -> None:
    with urllib.request.urlopen(SOURCE, timeout=30) as response:
        data = json.load(response)
    rows = []
    for c in data:
        code = c["cca2"]
        if not (c.get("unMember") or code in EXTRA):
            continue
        row = {"id": code.lower(), "en": c["name"]["common"], "ar": c["translations"]["ara"]["common"]}
        row.update(NAME_FIXES.get(code, {}))
        rows.append(row)
    rows.sort(key=lambda r: r["en"].casefold())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} places to {OUT}")


if __name__ == "__main__":
    main()
