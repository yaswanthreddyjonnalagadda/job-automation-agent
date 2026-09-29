"""A box inside a repeated Work or Education entry is answered from that entry's own record.

Steelcase (Avature), 29 September: every job repeats "Company / Position title / Is current position? /
Start date / End date" and every degree "Type of Degree / Area of Study / Country of Institution / Institution /
Status". Answered by label alone, a job's End date took the education end date and both degrees took the first
degree's school. The page below has Avature's structure with made-up details.
"""
from types import SimpleNamespace

import pytest

import config
import repeated_entries as r
from page_agent import PageAgent, parse_snapshot


def job_box(n, company="", start="", end=""):
    return f"""              - group [ref=w{n}]:
                  - generic [ref=w{n}a]: Company
                  - textbox "Company" [ref=w{n}b]:
                    - /placeholder: ""
{'                    - text: ' + company if company else ''}
                  - generic [ref=w{n}c]: Position title
                  - textbox "Position title" [ref=w{n}d]
                  - generic [ref=w{n}e]: Is current position?
                  - combobox "Is current position?" [ref=w{n}f]:
                    - option "Select an option" [selected]
                    - option "Yes"
                    - option "No"
                  - text: Start date
                  - textbox "Start date" [ref=w{n}g]{': ' + start if start else ''}
                  - text: End date
                  - textbox "End date" [ref=w{n}h]{': ' + end if end else ''}
                  - button "Remove" [ref=w{n}i]"""


def degree_box(n):
    return f"""              - group [ref=e{n}]:
                  - generic [ref=e{n}a]: Type of Degree
                  - combobox "Type of Degree" [ref=e{n}b]:
                    - option "Select an option" [selected]
                  - generic [ref=e{n}c]: Area of Study
                  - textbox "Area of Study" [ref=e{n}d]
                  - generic [ref=e{n}e]: Country of Institution
                  - combobox "Country of Institution" [ref=e{n}f]:
                    - option "Select an option" [selected]
                  - text: Institution
                  - combobox "Institution" [ref=e{n}g]
                  - generic [ref=e{n}h]: Status
                  - combobox "Status" [ref=e{n}i]:
                    - option "Select an option" [selected]"""


PAGE = "\n".join([
    '- main [ref=m1]:',
    '    - generic [ref=p1]:',
    '          - textbox "Home Phone" [ref=h1]',
    '          - textbox "Where?" [ref=h2]',
    '          - generic "Work Experience" [ref=s1]',
    job_box(1, "Globex", "2022-12-01"),          # a site's own resume reader put a date here
    job_box(2),
    job_box(3),
    '          - generic "Education History" [ref=s2]',
    degree_box(1),
    degree_box(2),
    '          - paragraph [ref=l0]: Languages',
    '          - group "Languages" [ref=l1]:',
    '              - combobox "Language" [ref=l2]',
    '              - combobox "Spoken Level" [ref=l3]',
])

HISTORY = {
    "experience": [
        {"company": "Acme", "title": "Network Engineer II", "start": "03/2024", "end": "", "current": True},
        {"company": "Globex", "title": "Network Engineer", "start": "01/2021", "end": "02/2024", "current": False},
        {"company": "Initech", "title": "NOC Analyst", "start": "06/2018", "end": "12/2020", "current": False},
    ],
    "education": [
        {"degree": "Master of Science", "field": "Computer Science", "school": "State University", "end": "05/2018"},
        {"degree": "Bachelor of Engineering", "field": "Electronics", "school": "City College", "end": "05/2016"},
    ],
}


def answers():
    found = {}
    for ref, entry in r.entry_map(PAGE).items():
        found[ref] = (entry.section, entry.index, entry.label, r.answer(entry, HISTORY))
    return found


def test_each_box_belongs_to_its_own_entry():
    found = answers()
    assert {ref: (s, i) for ref, (s, i, *_rest) in found.items() if ref.endswith("b")} == {
        "w1b": ("work", 0), "w2b": ("work", 1), "w3b": ("work", 2), "e1b": ("education", 0), "e2b": ("education", 1)}
    assert not any(ref.startswith(("h", "l")) for ref in found)   # outside the sections: not an entry


def test_an_entry_is_matched_by_the_company_it_shows_before_its_place():
    found = answers()
    # The first entry shows "Globex" (the site's reader put it there): it is Globex's record, not Acme's.
    assert found["w1g"][3][0] == "January 2021" and found["w1h"][3][0] == "February 2024"


def test_entries_without_a_company_take_the_records_left_over_and_the_current_job_is_one():
    found = answers()
    # Entry 1 shows Globex; entries 2 and 3 take Acme and Initech, in order -- not Globex again.
    assert found["w2b"][3][0] == "Acme" and found["w3b"][3][0] == "Initech"
    assert found["w2f"][3][0] == "Yes" and found["w3f"][3][0] == "No"
    value, _source, blank = found["w2h"][3]
    assert value == "" and blank                     # Acme is the current job: its end date stays blank


def test_each_degree_takes_its_own_school_and_nothing_is_guessed():
    found = answers()
    assert found["e1g"][3][0] == "State University" and found["e2g"][3][0] == "City College"
    assert found["e1f"][3][0] == "" and found["e1i"][3][0] == ""     # country and status: not in the record
    assert found["e2d"][3][0] == "Electronics"


def test_the_agent_answers_entry_boxes_from_the_entry_not_the_profile():
    job = SimpleNamespace(title="Engineer", company="Hooli", url="https://careers.example.com/apply")
    profile = config.UserProfile(email="jane@example.com", education_dates=(("State University", "", "May 2018"),))
    agent = PageAgent(SimpleNamespace(values=None), SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
                      profile, SimpleNamespace(raw_text="x"), job, resume_file=None)
    agent._ensure_state()
    agent.history = HISTORY
    agent._entries = r.entry_map(PAGE)
    boxes = {c.ref: c for c in parse_snapshot(PAGE)}
    assert agent.known_answer(boxes["w3h"])[0] == "December 2020"       # Initech's end, not the education's
    assert agent.known_answer(boxes["w2h"]) == ("", "work history (Acme)") and "w2h" in agent._entry_blank
    assert agent.known_answer(boxes["e2g"])[0] == "City College"
    assert agent.known_answer(boxes["e1f"]) == ("", "")                   # never the profile's country
