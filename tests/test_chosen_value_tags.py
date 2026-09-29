"""A choice shown as a tag beside an empty search box is the box's answer.

Rackspace (Workday), 29 September: Country Phone Code held "United States of America (+1)", shown as
"1 item selected, United States of America (+1)" and a tag beside the empty search box. The agent read the empty
box, "corrected" it four times, stopped with "could not set", and also asked the AI about "items selected" as if
it were a question. tests/fixtures/pages/workday_phone_code_tag.txt is that part of the page.
"""
from pathlib import Path
from types import SimpleNamespace

import config
import page_agent

PAGE = (Path(__file__).parent / "fixtures" / "pages" / "workday_phone_code_tag.txt").read_text(encoding="utf-8")


def controls(snapshot=PAGE):
    return page_agent.parse_snapshot(snapshot)


def test_the_tag_is_the_boxs_answer():
    box = next(c for c in controls() if c.name == "Country Phone Code")
    assert box.answer == "United States of America (+1)"


def test_the_list_of_tags_is_not_a_question():
    assert not [c for c in controls() if "selected" in c.question.lower()]
    assert [c.name for c in controls() if c.role == "textbox"] == ["Country Phone Code", "Phone Number", "Phone Extension"]


def test_nothing_chosen_leaves_the_box_empty():
    snapshot = PAGE.replace("1 item selected, United States of America (+1)", "0 items selected")
    snapshot = "\n".join(l for l in snapshot.splitlines() if "press delete" not in l and "paragraph [ref=f4e215]" not in l)
    box = next(c for c in controls(snapshot) if c.name == "Country Phone Code")
    assert box.answer == ""


def test_the_tag_alone_is_enough():
    snapshot = "\n".join(l for l in PAGE.splitlines() if "item selected" not in l)
    box = next(c for c in controls(snapshot) if c.name == "Country Phone Code")
    assert box.answer == "United States of America (+1)"


def test_an_answered_box_is_neither_filled_nor_corrected_again():
    job = SimpleNamespace(title="t", company="c", url="u")
    profile = config.UserProfile(email="jane@example.com", country="United States", phone_country_code="+1",
                                 phone_mobile="2175550142")
    agent = page_agent.PageAgent(SimpleNamespace(values=None), SimpleNamespace(),
                                 SimpleNamespace(auto_submit=False, ats_email=""), profile,
                                 SimpleNamespace(raw_text="x"), job, resume_file=None)
    touched = []
    agent.do = lambda page, answer, control: touched.append((control.name, answer.value)) or True
    agent.settle = lambda *a, **k: None
    agent.snapshot = lambda page: PAGE
    agent.locate = lambda page, ref: SimpleNamespace(get_attribute=lambda *a, **k: "", input_value=lambda **k: "",
                                                     evaluate=lambda *a, **k: "", count=lambda: 1)
    agent.tab = lambda page: SimpleNamespace(url="https://x.wd1.myworkdayjobs.com", wait_for_load_state=lambda *a, **k: None)
    agent.answer_what_is_known(page=None, controls=controls(), required=set())
    agent.correct_from_profile(None, page_agent.PagePlan(page_kind="application_form"), controls())
    assert not [t for t in touched if t[0] == "Country Phone Code"]


import pytest


@pytest.mark.parametrize("label, field", [
    ("Country Phone Code", ""), ("Phone Country Code", ""), ("Country Code", ""), ("Country Calling Code", ""),
    ("Country", "country"), ("Country/Region", "country"), ("Postal Code", "postal_code"), ("Zip Code", "postal_code"),
])
def test_a_dial_code_box_is_never_read_as_the_owners_country(label, field):
    assert page_agent._detail_field(label) == field


@pytest.mark.parametrize("child, picked", [
    ('textbox "Eastern Illinois University" [ref=e2]', "Eastern Illinois University"),   # Avature's tag
    ('textbox "Institution" [ref=e2]', ""),                  # the search box inside, named like the question
    ('textbox "Search" [ref=e2]', ""),
])
def test_a_picked_tag_inside_a_search_box_is_its_answer(child, picked):
    from page_agent import parse_snapshot
    snapshot = f'- combobox "Institution" [ref=e1] [cursor=pointer]:\n  - {child}:\n    - button [ref=e3]: ×'
    boxes = [c for c in parse_snapshot(snapshot) if c.role == "combobox"]
    assert boxes and boxes[0].value == picked
    if picked:
        assert not [c for c in parse_snapshot(snapshot) if c.name == picked]   # the tag is not a question
