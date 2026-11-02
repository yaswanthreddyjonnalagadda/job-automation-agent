"""A tick-box group is the question its named group says, not the placeholder of the dropdown above it (f296).

Fastly (Greenhouse), 1 November 2026: "With which of the following do you have professional experience? *" is a named
group of eleven tick boxes that sits right under a dropdown showing "Select...". The reader took the text before the
first box as the question, and "Select..." passes the test for words that introduce a choice ("select"). All eleven
boxes were named "Select...", the real question was lost, and the group was never put to the model.
"""
import pytest

import page_agent
from page_agent import parse_snapshot

GROUP_QUESTION = "With which of the following do you have professional experience? *"
BOXES = ["Go", "Rust", "Terraform", "Kubernetes / Docker", "None of these"]


def page(placeholder: str, question: str = GROUP_QUESTION, boxes=BOXES) -> str:
    lines = [
        "            - generic [ref=e1]:",
        "              - generic [ref=e2]:",
        f"                - generic [ref=e3]: {placeholder}",
        '                - combobox "How much experience do you have?" [ref=e4]',
        '              - button "Toggle flyout" [ref=e5] [cursor=pointer]',
        f'            - group "{question}" [ref=e6]:',
    ]
    for n, name in enumerate(boxes):
        lines += [f"              - generic [ref=e{10 + n * 3}]:",
                  f'                - checkbox "{name}" [ref=e{11 + n * 3}]',
                  f"                - generic [ref=e{12 + n * 3}]: {name}"]
    return "\n".join(lines) + "\n"


def boxes_of(snapshot: str):
    return [c for c in parse_snapshot(snapshot) if c.role == "checkbox"]


def test_the_fastly_group_is_named_by_its_own_question():
    boxes = boxes_of(page("Select..."))
    assert len(boxes) == len(BOXES)
    assert {b.group for b in boxes} == {GROUP_QUESTION}, "the boxes took the dropdown's placeholder for their question"
    assert all(b.question == GROUP_QUESTION for b in boxes)


@pytest.mark.parametrize("placeholder", ["Select...", "Select", "Select one", "Please select", "Choose one",
                                         "Choose an option", "-- Make a Selection --", "No selection", "Select an option"])
def test_no_placeholder_text_before_a_group_is_taken_for_its_question(placeholder):
    boxes = boxes_of(page(placeholder))
    assert {b.group for b in boxes} == {GROUP_QUESTION}, f"{placeholder!r} was read as the question of the group"


def test_a_real_introduction_still_names_boxes_that_have_no_group_of_their_own():
    """The existing rule is kept: loose boxes under 'Please select any certifications' are that question's choices."""
    snapshot = ("            - generic [ref=e1]: Please select any certifications you may have.\n"
                '            - checkbox "CCNA" [ref=e2]\n'
                '            - checkbox "CCNP" [ref=e3]\n'
                '            - checkbox "CCIE" [ref=e4]\n')
    boxes = boxes_of(snapshot)
    assert len(boxes) == 3
    assert {b.group for b in boxes} == {"Please select any certifications you may have."}
