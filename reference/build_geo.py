"""
Regenerate reference/geo.json from ISO 3166 (via pycountry).

    pip install -r requirements-dev.txt
    python reference/build_geo.py            # rewrites reference/geo.json
    python reference/build_geo.py --check    # exit 1 if geo.json is out of date

geo.json is data the agent reads at run time (through geo_reference.py);
this script is how it is made, so a change to the data is a reviewable
change to this file or to pycountry's version -- never a hand edit.

'names' are ISO's spellings (name, common name, official name). 'aliases'
are other spellings application forms use: "Korea, Republic of" read the
other way round, the name without accents, and the everyday names in
EXTRA below. Add a spelling here when a real form uses one the agent did
not recognise, with a test that shows the form.
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

import pycountry

OUT = Path(__file__).resolve().parent / "geo.json"

# Everyday spellings that ISO 3166 does not carry.
EXTRA = {
    "US": ["USA", "U.S.", "U.S.A.", "US"],
    "GB": ["UK", "U.K.", "Great Britain", "Britain"],
    "AX": ["Aaland Islands"],
    "AE": ["UAE"],
    "TR": ["Turkey"],
    "CZ": ["Czech Republic"],
    "CI": ["Ivory Coast"],
    "CV": ["Cape Verde"],
    "SZ": ["Swaziland"],
    "MM": ["Burma"],
    "MK": ["Macedonia"],
    "KR": ["South Korea", "Korea (South)"],
    "KP": ["North Korea", "Korea (North)"],
    "CD": ["Democratic Republic of the Congo", "Congo (DRC)", "DR Congo"],
    "CG": ["Republic of the Congo", "Congo (Republic)"],
    "VA": ["Vatican", "Vatican City"],
    "MO": ["Macau"],
    "PS": ["Palestine"],
    "RU": ["Russia"],
    "VN": ["Vietnam"],
    "LA": ["Laos"],
    "SY": ["Syria"],
    "IR": ["Iran"],
    "TZ": ["Tanzania"],
    "VE": ["Venezuela"],
    "BO": ["Bolivia"],
    "MD": ["Moldova"],
    "FM": ["Micronesia"],
    "TW": ["Taiwan"],
    "BN": ["Brunei"],
}

ABOUT = ("Place names, as data. Generated from ISO 3166-1 and ISO 3166-2:US "
         "(pycountry); 'names' are the ISO spellings (name, common name, official name), "
         "'aliases' are other spellings application forms use. Logic modules read places "
         "from here (geo_reference.py) and must not spell them in code.")


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def build() -> dict:
    countries = {}
    for c in pycountry.countries:
        names = [c.name]
        for attr in ("common_name", "official_name"):
            value = getattr(c, attr, None)
            if value and value not in names:
                names.append(value)
        aliases = []
        for name in names:
            if ", " in name:                                   # "Korea, Republic of"
                head, tail = name.split(", ", 1)
                aliases.append(f"{tail} {head}")
            if fold(name) != name:                             # "Åland Islands"
                aliases.append(fold(name))
        aliases.extend(EXTRA.get(c.alpha_2, []))
        aliases = [a for i, a in enumerate(aliases) if a not in names and a not in aliases[:i]]
        countries[c.alpha_2] = {"names": names, "aliases": aliases}

    us_states = {}
    for s in pycountry.subdivisions.get(country_code="US"):
        code = s.code.split("-", 1)[1]
        us_states[code] = {"names": [s.name], "aliases": [code]}

    return {
        "_about": ABOUT,
        "countries": dict(sorted(countries.items())),
        "us_states": dict(sorted(us_states.items())),
    }


def render(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if geo.json differs from a fresh build")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    text = render(build())
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != text:
            print(f"{args.out} is out of date: run python reference/build_geo.py", file=sys.stderr)
            return 1
        print(f"{args.out} is up to date (pycountry {getattr(pycountry, '__version__', '?')})")
        return 0
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out}: {len(json.loads(text)['countries'])} countries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
