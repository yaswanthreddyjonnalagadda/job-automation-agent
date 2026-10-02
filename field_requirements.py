"""Whether a field on a page needs an answer -- decided per field, with the evidence, in one place.

Until 1 October the question "does this blank field need the owner?" was answered in several places,
each from the whole page: one heading saying "Create a Career Profile account (optional)" anywhere on a
page made every password and one-time-code question on that page ignorable -- including a required
sign-in password in a different section. A field's requirement belongs to the field: the section it sits
in (the headings above it, by outline level), its own label ("*", "(optional)", "required"), and what the
planner reported. That is recorded here with the evidence that settled it, and `needs_owner` is the one
decision every caller asks.

Statuses:
    required     the field itself is marked required (or the planner says so) and no optional section holds it
    optional     the field, or a section that holds it, is marked optional, and nothing marks the field required
    conditional  marked required, but inside an optional section: required only if that section is used
    unknown      nothing on the page says either way
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from perception import is_secret_box

REQUIRED, OPTIONAL, CONDITIONAL, UNKNOWN = "required", "optional", "conditional", "unknown"
_STRICTNESS = {REQUIRED: 3, UNKNOWN: 2, CONDITIONAL: 1, OPTIONAL: 0}

FIELD_ROLES = ("textbox", "searchbox", "combobox", "listbox", "spinbutton", "radio", "checkbox", "switch",
               "radiogroup", "group", "button")

_LINE = re.compile(r'^(?P<indent>\s*)- (?P<role>[\w/-]+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?(?P<attrs>(?: \[[^\]]*\])*)'
                   r'(?::\s?(?P<value>.*))?$')
_OPTIONAL_WORD = re.compile(r"\(\s*optional\s*\)|\boptional\b", re.IGNORECASE)
# A mark on the label, not the word inside a question ("Is sponsorship required?" is not a required mark).
_REQUIRED_MARK = re.compile(r"\*\s*\)?\s*$|\(\s*required\s*\)|\brequired\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Field:
    ref: str
    role: str
    question: str
    sections: tuple[str, ...]          # the headings above it, outermost first
    line: int


@dataclass(frozen=True)
class Requirement:
    status: str
    evidence: str
    sections: tuple[str, ...] = field(default=())


def _unquote(text: str) -> str:
    return (text or "").replace('\\"', '"').replace("\\\\", "\\").strip()


def fields(snapshot: str) -> list[Field]:
    """Every control on the page with the outline of headings it sits under.

    A heading opens a section that runs to the next heading of the same or a higher level (an outline:
    an h3 under an h2 is inside it; the next h2 ends both)."""
    out: list[Field] = []
    outline: list[tuple[int, str]] = []          # (level, heading text)
    for number, raw in enumerate((snapshot or "").splitlines()):
        quoted = re.match(r"^(\s*)- '((?:[^']|'')*)'(:.*)?$", raw)
        if quoted:
            raw = f"{quoted.group(1)}- {quoted.group(2).replace(chr(39) * 2, chr(39))}{quoted.group(3) or ''}"
        m = _LINE.match(raw)
        if not m:
            continue
        role, attrs = m.group("role"), m.group("attrs") or ""
        name = _unquote(m.group("name") or "")
        if role == "heading":
            level_m = re.search(r"\[level=(\d+)\]", attrs)
            level = int(level_m.group(1)) if level_m else 6
            text = name or _unquote(m.group("value") or "")
            if not text:
                continue
            while outline and outline[-1][0] >= level:
                outline.pop()
            outline.append((level, text))
            continue
        ref = re.search(r"\[ref=([\w-]+)\]", attrs)
        if role in FIELD_ROLES and ref:
            out.append(Field(ref.group(1), role, name or _unquote(m.group("value") or ""),
                             tuple(t for _l, t in outline), number))
    return out


def requirement(question: str, sections: tuple[str, ...] = (), marked_required: bool = False) -> Requirement:
    """The requirement of one field from its own label, the sections that hold it, and the planner's word."""
    question = " ".join((question or "").split())
    own_optional = bool(_OPTIONAL_WORD.search(question))
    own_required = bool(_REQUIRED_MARK.search(question)) and not own_optional
    optional_section = next((s for s in sections if _OPTIONAL_WORD.search(s)), "")
    if own_optional:
        return Requirement(OPTIONAL, f"the field says it is optional: {question[:80]!r}", sections)
    if optional_section:
        if own_required or marked_required:
            return Requirement(CONDITIONAL, f"marked required, inside the optional section {optional_section[:80]!r}",
                               sections)
        return Requirement(OPTIONAL, f"inside the optional section {optional_section[:80]!r}", sections)
    if own_required:
        return Requirement(REQUIRED, f"the field is marked required: {question[:80]!r}", sections)
    if marked_required:
        return Requirement(REQUIRED, "the page planner reported it required", sections)
    return Requirement(UNKNOWN, "nothing on the page says whether it is required", sections)


def requirement_for(snapshot: str, question: str, ref: str = "", marked_required: bool = False) -> Requirement:
    """The requirement of the field a question names. When several fields carry the same words (a Password in
    an optional account section and another in a sign-in section) the strictest wins: a field is never let
    off because another one with the same label is optional."""
    found = fields(snapshot)
    matches = [f for f in found if ref and f.ref == ref] or [f for f in found if _same(f.question, question)]
    if not matches:
        return requirement(question, (), marked_required)
    candidates = [requirement(f.question or question, f.sections, marked_required) for f in matches]
    return max(candidates, key=lambda r: _STRICTNESS[r.status])


def needs_owner(req: Requirement, answered: bool) -> bool:
    """The one decision: a blank field stops for the owner only when it is required."""
    return req.status == REQUIRED and not answered


def account_password_fields(snapshot: str) -> list[tuple[Field, Requirement]]:
    """The secret boxes (password, passcode, one-time code) on a page, each with its own requirement."""
    return [(f, requirement(f.question, f.sections)) for f in fields(snapshot)
            if f.role in ("textbox", "searchbox") and is_secret_box(f.question)]


def secret_boxes_in_use(snapshot: str) -> int:
    """Secret boxes not inside an optional section: the ones that make a page an account step."""
    return sum(1 for _f, req in account_password_fields(snapshot) if req.status not in (OPTIONAL, CONDITIONAL))


def optional_section_of(snapshot: str) -> Optional[str]:
    """The optional section holding the page's secret boxes, when all of them are in one."""
    secrets = account_password_fields(snapshot)
    if not secrets or any(req.status not in (OPTIONAL, CONDITIONAL) for _f, req in secrets):
        return None
    return next((s for s in secrets[0][0].sections if _OPTIONAL_WORD.search(s)), None)


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _same(a: str, b: str) -> bool:
    a, b = _plain(a), _plain(b)
    return bool(a) and a == b
