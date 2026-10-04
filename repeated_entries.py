"""Which job or which degree a box belongs to, on a form that repeats one set of boxes per entry.

Steelcase (Avature), 29 September: the Work Experience section repeats "Company / Position title / Is current
position? / Start date / End date" once per job, and Education repeats "Type of Degree / Area of Study / Country
of Institution / Institution / Status". Answered by its label alone, a job's "End date" took the owner's
education end date and both degrees took the first degree's school. A box inside an entry is answered from that
entry's own record in the owner's work history (data/_experience.json) -- or not at all.

entry_map() reads the page's accessibility snapshot: a short title naming a work or education section starts
it; within it, the first box's label starts each entry, so its return starts the next. answer() gives one box
its value from the matching record: the record whose company (or degree) the entry already shows, else the
record in the same position.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

_SECTION_WORK = re.compile(r"^(?:work|employment|professional|job)\s+(?:experience|history)(?:\s+(\d+))?$|"
                           r"^experience(?:\s+(\d+))?$|^work history(?:\s+(\d+))?$", re.IGNORECASE)
_SECTION_EDU = re.compile(r"^education(?:al)?(?:\s+(?:history|background|details))?(?:\s+(\d+))?$", re.IGNORECASE)
_LINE = re.compile(r'^(\s*)- (\w+)(?: "((?:[^"\\]|\\.)*)")?([^:]*?)(?::\s*(.*))?$')
_FIELD_ROLES = {"textbox", "combobox", "listbox", "searchbox", "spinbutton", "checkbox", "radio", "button"}
_TITLE_ROLES = {"heading", "generic", "group", "region", "paragraph", "text", "legend", "form"}   # UKG names each entry's form "Work Experience 2"

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")


@dataclass
class Entry:
    section: str                 # "work" or "education"
    index: int                   # 0 for the first job or degree on the page
    label: str                   # the box's own label ("End date")
    values: dict = field(default_factory=dict)   # every box of this entry: label -> what it shows
    peers: list = field(default_factory=list)    # the values of every entry in this section, in page order


def _section_of(title: str) -> tuple[Optional[str], Optional[int]]:
    title = " ".join(title.split()).strip(" :*")
    for name, pattern in (("work", _SECTION_WORK), ("education", _SECTION_EDU)):
        found = pattern.fullmatch(title)
        if found:
            number = next((g for g in found.groups() if g), None)
            return name, (int(number) - 1 if number else None)
    return None, None


def entry_map(snapshot: str) -> dict[str, Entry]:
    """{ref: Entry} for every box inside a repeated work or education entry on the page."""
    found: dict[str, Entry] = {}
    section, index, first_label = None, -1, None
    section_indent = -1                              # where the entry's own title is drawn
    current: dict = {}
    peers: dict[str, list] = {"work": [], "education": []}
    last_box: Optional[tuple[int, str]] = None      # (indent, label) of the last box, whose value may follow
    for raw in (snapshot or "").splitlines():
        m = _LINE.match(raw)
        if not m:
            continue
        role, name, attrs, value = m.group(2), (m.group(3) or ""), m.group(4) or "", (m.group(5) or "").strip()
        ref = re.search(r"\[ref=([\w-]+)\]", attrs)
        indent = len(m.group(1))
        # A text box with a placeholder shows what it holds as a child line ("- text: Capital One").
        if last_box is not None and indent > last_box[0] and role == "text" and value and section:
            if not current.get(last_box[1].lower()):
                current[last_box[1].lower()] = value
            continue
        if last_box is not None and indent <= last_box[0]:
            last_box = None
        if role in _TITLE_ROLES and role not in _FIELD_ROLES:
            title = name or (value if role in ("heading", "legend") else "")
            if title and len(title.split()) <= 5:
                kind, numbered = _section_of(title)
                if kind:
                    if kind != section or numbered is not None:
                        index = numbered if numbered is not None else -1
                        first_label, current = None, {}
                    section, section_indent = kind, indent
                    if numbered is not None:
                        first_label, current = "", {}
                    continue
                # A heading or named group drawn inside the entry (UKG's "From" / "To" date groups) is part of
                # it; only one at the entry's own level or above starts another section (Languages ...).
                inside = section is not None and indent > section_indent
                if not inside and role in ("heading", "legend") or (not inside and name and role in ("group", "region") and section
                                                     and not re.search(r"remove|add", title, re.IGNORECASE)):
                    section = None                     # another section starts: Languages, Attachments ...
                    continue
        if section is None or role not in _FIELD_ROLES or not ref or not name:
            continue
        if role == "button" and re.search(r"\b(remove|delete|add|save|cancel)\b", name, re.IGNORECASE):
            continue
        label = " ".join(name.split())
        if first_label is None or first_label == "":
            if first_label is None:
                index += 1
            first_label, current = label, {}
        elif label == first_label:
            index += 1
            current = {}
        current[label.lower()] = value
        section_peers = peers[section]
        while len(section_peers) <= max(index, 0):
            section_peers.append({})
        section_peers[max(index, 0)] = current
        found[ref.group(1)] = Entry(section, max(index, 0), label, current, section_peers)
        last_box = (indent, label)
    return found


def _month_year(text: str) -> str:
    """'02/2025' -> 'February 2025' (the form's own date box takes it from there); anything else as it is."""
    found = re.fullmatch(r"\s*(\d{1,2})[/-](\d{4})\s*", text or "")
    if found and 1 <= int(found.group(1)) <= 12:
        return f"{MONTHS[int(found.group(1)) - 1]} {found.group(2)}"
    return (text or "").strip()


def _same(a: str, b: str) -> bool:
    a, b = (re.sub(r"[^a-z0-9 ]", " ", (x or "").lower()) for x in (a, b))
    a, b = " ".join(a.split()), " ".join(b.split())
    return bool(a and b) and (a == b or a in b or b in a)


def _shown(values: dict, section: str) -> list[str]:
    """What an entry already shows that says which record it is: a job's company; a degree's school, degree or
    field. A degree is known by its school first -- "Master of Science (MS)" alone did not match the record's
    "Master's", the entry looked unclaimed, and a new entry took the same master's (UKG, 30 September)."""
    keys = ("company", "employer", "organization") if section == "work" else (
        "school", "institution", "university", "college", "degree", "area of study", "field", "major")
    return [v for k, v in values.items() if v and any(word in k for word in keys)]


def _names(record: dict, section: str) -> tuple[str, ...]:
    return (record.get("company", ""),) if section == "work" else (
        record.get("school", ""), record.get("degree", ""), record.get("field", ""))


def _identifies(shown: str, name: str) -> bool:
    """The same company, school or degree, in the words of either -- "Master of Science (MS)" is "Master's"."""
    if not shown or not name:
        return False
    if _same(shown, name):
        return True
    import option_match
    return option_match.best_option([name], shown) is not None or option_match.best_option([shown], name) is not None


def with_profile(history: dict, profile) -> dict:
    """The work history, with explicit profile subjects and missing education dates applied.

    The resume reader keeps only a degree's end; the owner's profile holds both ends ("JNTU Hyderabad, August
    2015, April 2019"). UKG's degree entries asked From and To, the history had no start, and the run stopped for
    the owner to type dates the profile already held (30 September). A date the history has is kept."""
    history = dict(history or {})
    dated = [tuple(d) for d in (getattr(profile, "education_dates", ()) or ()) if d and len(d) >= 3]
    profile_degrees = [tuple(row) for row in (getattr(profile, "education", ()) or ()) if row and len(row) >= 3]
    degrees = []
    for record in history.get("education") or []:
        record = dict(record)
        candidates = [row for row in profile_degrees if _identifies(str(row[2]), str(record.get("school") or ""))]
        if len(candidates) > 1:
            candidates = [row for row in candidates if _identifies(str(row[0]), str(record.get("degree") or ""))]
        if len(candidates) == 1 and str(candidates[0][1]).strip():
            record["field"] = str(candidates[0][1]).strip()
        for school, start, end in (d[:3] for d in dated):
            if _identifies(str(school or ""), str(record.get("school") or "")):
                record["start"] = record.get("start") or start
                record["end"] = record.get("end") or end
                break
        degrees.append(record)
    history["education"] = degrees
    return history


def record_for(entry: Entry, history: dict) -> Optional[dict]:
    """The owner's record for this entry.

    Entries that already show a company (or degree) take the record that names it; the others take the records
    left over, in page order. A site's resume reader often fills the first entry and not the rest: taken by place
    alone, the second entry got the same job again and the current job got none."""
    records = list((history or {}).get("experience" if entry.section == "work" else "education") or [])
    peers = entry.peers or [entry.values]
    claimed: dict[int, int] = {}
    for position, values in enumerate(peers):
        shown = _shown(values, entry.section)
        if not shown:
            continue
        for number, record in enumerate(records):
            if number not in claimed.values() and any(_identifies(s, name) for s in shown
                                                      for name in _names(record, entry.section)):
                claimed[position] = number
                break
    left = [n for n in range(len(records)) if n not in claimed.values()]
    for position in range(len(peers)):
        if position not in claimed and left:
            claimed[position] = left.pop(0)
    number = claimed.get(entry.index)
    return records[number] if number is not None else None


def describe(entry: Optional[Entry], history: dict) -> str:
    """The entry in words, for a question asked about one of its boxes: "Masters of Science in Computer
    Technology, Eastern Illinois University" or "Network Engineer, Globex (2021-2024)". Empty if unknown."""
    if entry is None:
        return ""
    record = record_for(entry, history) or {}
    if entry.section == "education":
        degree = " in ".join(p for p in (record.get("degree", ""), record.get("field", "")) if p)
        return ", ".join(p for p in (degree, record.get("school", "")) if p)
    span = "-".join(p for p in (str(record.get("start", ""))[-4:], "present" if record.get("current")
                                else str(record.get("end", ""))[-4:]) if p)
    words = ", ".join(p for p in (record.get("title", ""), record.get("company", "")) if p)
    return f"{words} ({span})" if words and span else words


# What a label asks for, in an entry of each kind: (pattern, the record's field).
_WORK_FIELDS = (
    (r"\bcurrent(ly)?\b|\bstill\b|present", "current"),       # before "title": "Is current position?"
    (r"\b(company|employer|organi[sz]ation)\b", "company"),
    (r"\b(position|job)?\s*title\b|\bposition\b|\brole\b", "title"),
    (r"\b(start|from|began|begin)\b", "start"),
    (r"\b(end|to|until|left)\b", "end"),
    (r"\b(location|city)\b", "location"),
)
_EDUCATION_FIELDS = (
    (r"\bcountry\b", None),                       # not in the record: never guessed from another box
    (r"\bstatus\b|\bgpa\b|\bgrade\b|\bhonou?rs?\b", None),
    (r"\b(institution|school|university|college)\b", "school"),
    (r"\b(area of study|field|major|subject|specializ|discipline)\b", "field"),
    (r"\b(degree|qualification|level)\b", "degree"),
    (r"\b(start|from|began|begin|enrol)", "start"),
    (r"\b(end|graduat|complet|finish|to)\b", "end"),
)


def answer(entry: Entry, history: dict) -> tuple[str, str, bool]:
    """(value, source, leave_blank) for one box of an entry.

    leave_blank is True when the empty answer is the right one -- the end date of the job the owner still has --
    so the box is not handed to anyone else to fill."""
    record = record_for(entry, history)
    if record is None:
        return "", "", False
    label = entry.label
    for pattern, key in (_WORK_FIELDS if entry.section == "work" else _EDUCATION_FIELDS):
        if not re.search(pattern, label, re.IGNORECASE):
            continue
        if key is None:
            return "", "", False
        source = f"work history ({record.get('company') or record.get('school') or 'entry ' + str(entry.index + 1)})"
        if key == "current":
            return ("Yes" if record.get("current") else "No"), source, False
        if key == "end" and entry.section == "work" and record.get("current"):
            return "", source, True
        if key in ("start", "end"):
            value = _month_year(str(record.get(key) or ""))
            # A box for one part of the date ("From month", "To year"): that part. UKG's "From month" list took
            # "February 2025", matched none of its rows, and was answered "Choose..." by the AI (30 September).
            part = re.search(r"\b(month|year|day)\s*$", label, re.IGNORECASE)
            if part and value:
                import concept_matcher
                value = concept_matcher._date_part(value, part.group(1).lower()) or value
            return value, source, False
        return str(record.get(key) or "").strip(), source, False
    return "", "", False


_DATE = re.compile(r"(?:(?:%s)[a-z]*\.?\s*)?(?:\d{4})?|\d{1,2}[/-]\d{4}" % "|".join(m[:3] for m in MONTHS),
                   re.IGNORECASE)


def is_date(value: str) -> bool:
    """A month, a year, or both ("July", "2021", "July 2021", "07/2021")."""
    value = (value or "").strip()
    return bool(value) and bool(_DATE.fullmatch(value))


def understands(entry: Entry) -> bool:
    """Whether the record can say what this box of an entry holds: its label names one of the entry's fields. A bare
    "Year" or "Month" (Workday's date parts, one under "From" and one under "To") does not say which date it is."""
    fields = _WORK_FIELDS if entry.section == "work" else _EDUCATION_FIELDS
    return any(re.search(pattern, entry.label or "", re.IGNORECASE) for pattern, _key in fields)
