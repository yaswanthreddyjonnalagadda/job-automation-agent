"""A date made of Month, Day and Year boxes is written in that order, whatever order the answers arrived in.

Copart's 'desired start date' is three spinbuttons. Run against the real widget, the agent's own filler gave a clean
10/14/2026 only for Month, Day, Year; every other order left rubbish ('12', '1214', '1012'), and the live planner
delivered the three answers in another order, leaving 'Invalid Date: 00/06/2026' (f151). Each box types into the
next, so the order is the date.
"""
import itertools

import pytest

from page_agent import Answer, Control, PageAgent

PARTS = ("Month", "Day", "Year")


def box(ref, name):
    return Control(ref=ref, role="spinbutton", name=name)


def answer(ref, question, value="x"):
    return Answer(ref, question, "fill", value, "profile")


def controls_for(*dates):
    out = [Control(ref="q0", role="textbox", name="Compensation")]
    for d, _ in enumerate(dates):
        out += [box(f"d{d}{p[0]}", p) for p in PARTS]
        out.append(Control(ref=f"t{d}", role="textbox", name=f"Note {d}"))
    return out


def refs(answers):
    return [a.ref for a in answers]


@pytest.mark.parametrize("order", list(itertools.permutations(PARTS)))
def test_every_arrival_order_is_written_month_day_year(order):
    controls = controls_for(0)
    given = [answer(f"d0{p[0]}", p) for p in order]
    assert refs(PageAgent._date_segments_in_order(given, controls)) == ["d0M", "d0D", "d0Y"]


def test_other_answers_keep_their_places():
    controls = controls_for(0)
    given = [answer("q0", "Compensation"), answer("d0Y", "Year"), answer("t0", "Note 0"),
             answer("d0D", "Day"), answer("d0M", "Month")]
    assert refs(PageAgent._date_segments_in_order(given, controls)) == ["q0", "d0M", "t0", "d0D", "d0Y"]


def test_two_dates_on_one_page_stay_separate():
    controls = controls_for(0, 1)
    given = [answer("d1Y", "Year"), answer("d0D", "Day"), answer("d1M", "Month"), answer("d0Y", "Year"),
             answer("d1D", "Day"), answer("d0M", "Month")]
    result = refs(PageAgent._date_segments_in_order(given, controls))
    first, second = [r for r in result if r.startswith("d0")], [r for r in result if r.startswith("d1")]
    assert first == ["d0M", "d0D", "d0Y"] and second == ["d1M", "d1D", "d1Y"]


def test_a_year_only_box_and_a_spinbutton_that_is_not_a_date_are_left_alone():
    controls = [Control(ref="y", role="spinbutton", name="Year"),
                Control(ref="n", role="spinbutton", name="Years of experience")]
    given = [answer("n", "Years of experience"), answer("y", "Year")]
    assert refs(PageAgent._date_segments_in_order(given, controls)) == ["n", "y"]


def test_a_plan_with_no_dates_comes_back_unchanged():
    controls = controls_for(0)
    given = [answer("t0", "Note 0"), answer("q0", "Compensation")]
    assert PageAgent._date_segments_in_order(given, controls) == given
