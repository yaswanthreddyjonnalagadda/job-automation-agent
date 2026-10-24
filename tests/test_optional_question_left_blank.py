"""An optional question the form cannot take is left blank, not a reason to stop the run (f185).

Invesco's Voluntary Disclosures: 'Veteran Status' is optional and its list has no 'I am not a veteran' row (Active Duty
Wartime or Campaign Badge Veteran, ..., Not Applicable, Recently Separated Veteran, I Am a Protected Veteran). The agent
tried, the answer would not stay, and the whole run stopped for the owner -- on a question the application does not require.
A required question that cannot be set still stops the run.
"""
from types import SimpleNamespace

import page_agent

SNAPSHOT = """- group [ref=e1]:
  - generic [ref=e2]: Gender*
  - button "Gender Male Required" [ref=e3]: Male
  - generic [ref=e4]: Race/Ethnicity
  - button "Race/Ethnicity Asian (United States of America)" [ref=e5]: Asian (United States of America)
  - generic [ref=e6]: Veteran Status
  - button "Veteran Status Select One" [ref=e7]: Select One
"""


def agent():
    a = page_agent.PageAgent.__new__(page_agent.PageAgent)
    a.notes = []
    return a


def controls():
    return page_agent.parse_snapshot(SNAPSHOT)


def test_an_optional_blank_question_is_left_blank_with_a_note():
    a = agent()
    kept = a._drop_optional(["Veteran Status Select One = 'I am not a veteran'"], controls())
    assert kept == []
    assert any("Veteran Status" in n and "left blank" in n for n in a.notes)


def test_a_required_question_still_stops_the_run():
    a = agent()
    kept = a._drop_optional(["Gender Male Required = 'Female'"], controls())
    assert kept == ["Gender Male Required = 'Female'"]
    assert a.notes == []


def test_an_optional_question_that_holds_another_value_is_not_dropped():
    """A wrong value left in an optional field is worse than a stop: only a blank one is let go."""
    a = agent()
    kept = a._drop_optional(["Race/Ethnicity Asian (United States of America) = 'White'"], controls())
    assert kept == ["Race/Ethnicity Asian (United States of America) = 'White'"]


def test_a_question_that_is_not_on_the_page_keeps_its_stop():
    a = agent()
    assert a._drop_optional(["Something else = 'x'"], controls()) == ["Something else = 'x'"]
