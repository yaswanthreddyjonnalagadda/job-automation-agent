"""Lever/Palantir form: required marks, town suggestions, and the date under a self-identification name (f231).

Seen on a live Palantir run: the star sat on its own line after the label, so no question was read as required and
every optional box (Preferred Name, Name Pronunciation) was sent to a model and written up as an essay; the Current
location suggestion list was left open because "Fairfax, Virginia, United States" matched neither of "Fairfax, VA, USA"
and "Fairfax, Waterford, VA, USA"; and the Date under the disability form's Name stayed empty.
"""
from types import SimpleNamespace

import config
import geo_reference
import option_match
import page_agent

SNAPSHOT = """
- generic [ref=e1]:
  - listitem [ref=e2]:
    - generic [ref=e3]:
      - generic [ref=e4]:
        - text: Full name
        - generic [ref=e5]: ✱
      - textbox "Full name ✱" [ref=e6]
  - listitem [ref=e7]:
    - generic [ref=e8]:
      - generic [ref=e9]: Phone
      - textbox "Phone" [ref=e10]
  - listitem [ref=e11]:
    - generic [ref=e12]:
      - generic [ref=e13]:
        - text: Why do you want to work here, and what would you bring to the role in the long term?
        - generic [ref=e14]: ✱
      - textbox [ref=e15]
"""


def test_a_star_on_its_own_line_after_the_label_marks_the_question_required():
    required = page_agent.required_questions(SNAPSHOT)
    assert "Full name" in required
    assert "Why do you want to work here, and what would you bring to the role in the long term?" in required
    assert not any("Phone" in q for q in required)


def test_optional_boxes_are_then_left_blank_and_the_required_ones_kept():
    required = page_agent.required_questions(SNAPSHOT)
    todo, blank = page_agent.split_optional(["Phone", "Why do you want to work here, and what would you bring to the role in the long term?*"], required)
    assert blank == ["Phone"] and len(todo) == 1


def test_a_town_in_one_spelling_is_the_same_town_in_another_but_not_a_neighbouring_place():
    rows = ["Fairfax, VA, USA", "Fairfax, Waterford, VA, USA", "Fairfax, Marshall, SK, CAN"]
    assert option_match.best_option(rows, "Fairfax, Virginia, United States") == 0
    assert option_match.best_option(rows, "Fairfax, Virginia") == 0
    assert geo_reference.same_locality("Springfield, IL", "Springfield, Illinois, United States")
    assert not geo_reference.same_locality("Springfield, IL", "Springfield, MO")


def agent():
    import test_page_agent
    return test_page_agent.make_agent(None, None, profile=config.UserProfile(first_name="Pat", last_name="Doe"))


def test_the_date_under_a_self_identification_name_is_the_day_it_is_filled_in():
    control = page_agent.Control(ref="e1", role="textbox", name="Date", container="Voluntary self-identification of disability")
    value, source = agent().known_answer(control)
    assert value and source == "profile.application_date"


def test_a_bare_date_anywhere_else_is_not_guessed():
    control = page_agent.Control(ref="e1", role="textbox", name="Date", container="Education")
    assert agent().known_answer(control)[0] == ""


def test_a_lists_instruction_row_is_its_placeholder_not_an_answer():
    snapshot = ('- generic [ref=e1]:\n  - generic [ref=e2]:\n    - text: Which university did you attend?\n'
                '    - combobox [ref=e3]:\n'
                '      - option "Click Here (If you encounter an issue, try clearing cache & cookies)" [selected]\n'
                '      - option "Aalto University"\n      - option "Eastern Illinois University"\n')
    box = next(c for c in page_agent.parse_snapshot(snapshot) if c.role == "combobox")
    assert box.answer == ""
    assert option_match.best_option(list(box.options), "Eastern Illinois University") is not None
    assert option_match.is_placeholder("Tap here to choose")
