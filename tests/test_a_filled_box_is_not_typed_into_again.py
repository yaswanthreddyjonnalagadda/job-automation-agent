"""A box that already shows the answer is not typed into again, and a fill that fails says why (f305).

Rackspace's Workday, 2 November 2026: the site's own resume autofill had written the owner's name, address, e-mail and
phone while the agent was reading. The agent then typed each again, waited out a 2.5 second timeout on a box that had
been redrawn, and marked eight boxes "failed" -- 3 to 4 seconds each -- although every one held the right value.
"""
import json
from types import SimpleNamespace

import pytest

import field_timing
import page_agent
from test_action_verification import agent, browser, control_for, page  # noqa: F401


@pytest.fixture()
def timings(tmp_path, monkeypatch):
    target = tmp_path / "timings.jsonl"
    monkeypatch.setenv("JAA_FIELD_TIMINGS", str(target))
    return lambda: [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]


def test_a_box_that_already_holds_the_answer_is_left_alone_and_says_so(page, monkeypatch, timings):
    page.set_content('<label>First Name <input id="f" value="Yaswanth Reddy"></label>')
    a = agent()
    control = control_for(a, page, "textbox", "First Name")

    def never(*args, **kwargs):
        raise AssertionError("the box was typed into although it already held the answer")
    monkeypatch.setattr(page_agent, "fill_and_dispatch", never)
    answer = page_agent.Answer(control.ref, control.question, "fill", "Yaswanth Reddy", "profile")
    assert a.do(page, answer, control) is True
    (row,) = [r for r in timings() if r["kind"] == "field"]
    assert row["result"] == "ok" and row["why"] == "already holds the answer"


def test_a_box_holding_something_else_is_still_filled(page, timings):
    page.set_content('<label>First Name <input id="f" value="Somebody Else"></label>')
    a = agent()
    control = control_for(a, page, "textbox", "First Name")
    answer = page_agent.Answer(control.ref, control.question, "fill", "Yaswanth Reddy", "profile")
    assert a.do(page, answer, control) is True
    assert page.input_value("#f") == "Yaswanth Reddy"


def test_a_failed_fill_records_why_without_the_value(page, monkeypatch, timings):
    page.set_content('<label>Zip <input id="z" value=""></label>')
    a = agent()
    control = control_for(a, page, "textbox", "Zip")

    def times_out(*args, **kwargs):
        raise TimeoutError("Locator.fill: Timeout 2500ms exceeded.\nCall log:\n  - fill(\"22031\")")
    monkeypatch.setattr(page_agent, "fill_and_dispatch", times_out)
    answer = page_agent.Answer(control.ref, control.question, "fill", "22031", "profile")
    try:
        a.do(page, answer, control)
    except Exception:
        pass
    rows = [r for r in timings() if r["kind"] == "field"]
    assert rows and rows[-1]["why"].startswith("fill: TimeoutError: Locator.fill: Timeout 2500ms")
    assert "22031" not in rows[-1]["why"]


def test_a_box_holding_only_part_of_the_answer_is_still_put_right(page):
    """tests/test_page_agent.py::test_the_name_is_put_right_from_the_profile: "Yaswanth" is not "Yaswanth Reddy"."""
    page.set_content('<label>First Name <input id="f" value="Yaswanth"></label>')
    a = agent()
    control = control_for(a, page, "textbox", "First Name")
    answer = page_agent.Answer(control.ref, control.question, "fill", "Yaswanth Reddy", "profile")
    assert a.do(page, answer, control) is True
    assert page.input_value("#f") == "Yaswanth Reddy"


@pytest.mark.parametrize("shown, want, same", [
    ("Yaswanth Reddy", "yaswanth  reddy", True),
    ("(571) 354-5212", "+1 571 354 5212", True),
    ("Yaswanth", "Yaswanth Reddy", False),
    ("Fairfax, VA", "Fairfax", False),
    ("Computer Science", "Computer Technology", False),
])
def test_what_counts_as_already_showing_the_answer(shown, want, same):
    assert page_agent._shows_the_answer(shown, want) is same
