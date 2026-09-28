"""Place names are data, never logic (RFC-001, principle "data never lives in logic").

On 23 September a failing run was "fixed" by writing "afghanistan" and
"badakhshan" into five decision points, and one owner's country, state and
city into several more. That fixed one form for one person and broke the
rule it lived in. This test fails the build if a place name comes back as a
literal in a logic module: places live in reference/geo.json, read through
geo_reference.py, and come from the owner's profile.

What counts as a place: every country and US state the agent knows
(reference/geo.json) and every province, region or state of any country in
ISO 3166-2 (pycountry, a test-only dependency) -- "Badakhshan province" as
much as "Afghanistan".

What counts as a literal: a string constant that *is* a place (tuples of
"wrong" values, defaults, comparison targets), and a quoted place inside a
larger string (JavaScript such as val.includes('...')). Docstrings and
comments may name places as examples; tests, the reference data and its
loader are not logic modules.
"""

from __future__ import annotations

import ast
import re
import sys
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import geo_reference  # noqa: E402

pycountry = pytest.importorskip("pycountry", reason="pip install -r requirements-dev.txt")

# The agent's own code: the modules at the root and the site adapters. A new
# package of logic is added here. (Listing them, rather than walking the
# whole tree, keeps virtual environments and local folders out of the scan.)
LOGIC_DIRS = (ROOT, ROOT / "sites")
EXEMPT = {"geo_reference.py"}          # the reference data's own loader
QUOTED = re.compile(r"""['"]([^'"\n]{4,60})['"]""")

# Words that name a place somewhere and mean something else in this code:
# Malé, the Maldives' capital region, is spelled like a gender answer.
ORDINARY_WORDS = {"male"}

# "Badakhshan Province", "Punjab State", "Kabul District" name the same place.
_ADMIN_SUFFIX = re.compile(r"\s+(province|state|region|district|county|governorate|prefecture|oblast|"
                           r"department|municipality|parish|territory)$")


@lru_cache(maxsize=1)
def places() -> frozenset[str]:
    names = set(geo_reference.place_names())
    for subdivision in pycountry.subdivisions:
        norm = geo_reference.normalize(subdivision.name)
        if len(norm) > 3:
            names.add(norm)
    return frozenset(names - ORDINARY_WORDS)


def is_place(text: str) -> bool:
    norm = geo_reference.normalize(text)
    return norm in places() or _ADMIN_SUFFIX.sub("", norm) in places()


def logic_modules():
    for folder in LOGIC_DIRS:
        for path in sorted(folder.glob("*.py")):
            if path.name not in EXEMPT:
                yield path


def test_the_scan_sees_the_agents_code():
    names = {path.name for path in logic_modules()}
    assert {"page_agent.py", "browser_automation.py", "interaction.py", "safety.py", "workday.py"} <= names


def docstring_nodes(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                yield body[0].value


def place_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = {id(node) for node in docstring_nodes(tree)}
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str) or id(node) in skip:
            continue
        text = node.value
        if is_place(text):
            found.append(f"{path.name}:{node.lineno}: {text!r}")
            continue
        for quoted in QUOTED.findall(text):
            if is_place(quoted):
                found.append(f"{path.name}:{node.lineno}: {quoted!r} inside a string")
    return found


def test_no_logic_module_spells_a_place():
    offenders = [hit for path in logic_modules() for hit in place_literals(path)]
    assert offenders == [], (
        "Place names belong in reference/geo.json and the owner's profile, not in logic:\n  "
        + "\n  ".join(offenders))


# Lines as the 23 September commit (0e9f9ea) wrote them, one per decision point.
_PATCH_LINES = [
    # safety.py, page_agent.py (twice), browser_automation.py
    'if val_clean in ("afghanistan", "badakhshān", "badakhshan", "badakhshan province"):\n    pass\n',
    # interaction.py, the sweep's JavaScript
    'JS = """} else if (val.includes(\'badakhsh\') || val.includes(\'badakhshan\')) {"""\n',
    # the owner's own address as everyone's fallback
    'country = profile.get("country") or "United States"\n',
    'state = profile.get("state") or "Virginia"\n',
]


@pytest.mark.parametrize("line", _PATCH_LINES)
def test_the_guard_would_have_caught_the_23_september_patch(tmp_path, line):
    """The check is only worth having if it fails on what it exists to stop."""
    sample = tmp_path / "sample.py"
    sample.write_text(line, encoding="utf-8")
    assert place_literals(sample), f"not flagged: {line!r}"


def test_the_guard_does_not_flag_ordinary_answers(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text('GENDERS = ("Male", "Female", "Decline to answer")\n'
                      'LABEL = "Country / Region"\n'
                      'HINT = "Select your state"\n', encoding="utf-8")
    assert place_literals(sample) == []
