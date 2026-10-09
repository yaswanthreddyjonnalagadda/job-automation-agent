"""Field-local evidence for optional account passwords in accessibility snapshots.

A nearby heading is evidence only within its section. It cannot waive a password
in a sibling section, or a code needed to verify an application. Values are never
retained here. Unknown fields remain subject to the normal account/review gates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


_NODE = re.compile(r'^(?P<indent>\s*)- (?P<role>[\w/-]+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?'
                   r'(?P<attrs>(?: \[[^\]]*\])*)(?::\s?(?P<value>.*))?$')
_PASSWORD = re.compile(r"pass ?word", re.IGNORECASE)
_CODE_PASSWORD = re.compile(r"\b(?:one[-\s]?time|verification|security|authentication)\s+pass\s?word\b|"
                            r"\b(?:otp|code|passcode|pin)\b",
                            re.IGNORECASE)
_PASSWORD_LABEL = re.compile(
    r"^(?:(?:enter|re[- ]?enter|confirm|verify|repeat|retype|new|current|your|account)\s+)*pass\s?word"
    r"(?:\s*(?:\*|\(required\)|\(optional\)|:))*$", re.IGNORECASE)
_CONTAINERS = {"group", "region", "form", "dialog"}
_BOUNDARIES = {"iframe", "document", "dialog"}


def optional_account_section(name: str) -> bool:
    return bool(re.search(r"\baccount\b", name, re.IGNORECASE)
                and re.search(r"\boptional\b", name, re.IGNORECASE)
                and not re.search(r"\b(?:not\s+|non[-\s]?)optional\b", name, re.IGNORECASE))


@dataclass(frozen=True)
class PasswordField:
    ref: str
    question: str
    optional_account: bool
    section: str


@dataclass(frozen=True)
class _Section:
    index: int
    indent: int
    parent: int | None
    level: int | None
    name: str


def account_password_fields(snapshot: str) -> list[PasswordField]:
    """Associate passwords with heading or named-container evidence.

    A heading ends at a peer/higher heading or when its containing node ends.
    Headings without levels and named sibling containers end the preceding scope
    conservatively. Subheadings can remain within an optional account section.
    An unnamed input can use a preceding password label inside the same container;
    a label is consumed once and cannot cross a section or container boundary.
    """
    fields: list[PasswordField] = []
    ancestors: list[tuple[int, int, str, str]] = []  # indent, node index, optional section name, role
    headings: list[_Section] = []
    label: tuple[int | None, str] | None = None
    for index, raw in enumerate((snapshot or "").splitlines()):
        quoted = re.match(r"^(\s*)- '((?:[^']|'')*)'(:.*)?$", raw)
        if quoted:
            raw = f"{quoted.group(1)}- {quoted.group(2).replace(chr(39) * 2, chr(39))}{quoted.group(3) or ''}"
        node = _NODE.fullmatch(raw)
        if not node:
            continue
        indent, role = len(node["indent"]), node["role"]
        name = (node["name"] or "").replace('\\"', '"').replace("\\\\", "\\")
        attrs = node["attrs"] or ""
        while ancestors and ancestors[-1][0] >= indent:
            ancestors.pop()
        parent_ids = {item[1] for item in ancestors}
        boundary = next((i for _depth, i, _name, ancestor_role in reversed(ancestors)
                         if ancestor_role in _BOUNDARIES), -1)
        if label is not None and label[0] is not None and label[0] not in parent_ids:
            label = None
        headings = [h for h in headings if indent >= h.indent and (h.parent is None or h.parent in parent_ids)]
        if role in _BOUNDARIES:
            label = None
        elif role in _CONTAINERS and name:
            headings = [h for h in headings if h.indent < indent]
            label = None
        if role == "heading":
            label = None
            level_match = re.search(r"\[level=(\d+)\]", attrs)
            level = int(level_match[1]) if level_match else None
            while headings and headings[-1].index > boundary \
                    and (level is None or headings[-1].level is None or headings[-1].level >= level):
                headings.pop()
            headings.append(_Section(index, indent, ancestors[-1][1] if ancestors else None,
                                     level, name or node["value"] or ""))
        question = name or (label[1] if label is not None else "")
        if role == "textbox" and _PASSWORD.search(question) and not _CODE_PASSWORD.search(question):
            evidence = [(h.index, h.name) for h in headings
                        if h.index > boundary and optional_account_section(h.name)]
            evidence += [(i, section) for _depth, i, section, _role in ancestors if i >= boundary and section]
            section = max(evidence)[1] if evidence else ""
            ref = re.search(r"\[ref=([\w-]+)\]", attrs)
            fields.append(PasswordField(ref[1] if ref else "", question, bool(section), section))
        if role in ("textbox", "searchbox", "combobox", "spinbutton", "checkbox", "radio", "button"):
            label = None
        elif role in ("generic", "text", "paragraph", "strong", "emphasis") and (name or node["value"]):
            text = (name or node["value"]).strip()
            label = (ancestors[-1][1] if ancestors else None, text) if _PASSWORD_LABEL.fullmatch(text) else None
        section = name if role in _CONTAINERS and optional_account_section(name) else ""
        ancestors.append((indent, index, section, role))
    return fields
