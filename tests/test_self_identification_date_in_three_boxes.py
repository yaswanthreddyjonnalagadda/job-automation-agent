"""The date under the name on a self-identification form is today's, drawn as one box or as Month / Day / Year (f305).

Rackspace's Workday, 2 November 2026: the Voluntary Self-Identification of Disability form (CC-305) draws its required
Date as three boxes in a group named Date. The agent knew a bare "Date" on such a form is the day it is filled in, but the
three boxes are called Month, Day and Year, so each came back unanswered, was left blank as "optional", and the form
would not go on ("The field Date is required and must have a value") -- the run stopped for the owner. Palantir stopped
the same way ("required fields still blank: Date").
"""
from datetime import date
from types import SimpleNamespace

import pytest

import config
import page_agent
from browser_automation import JobApplicationAssistant

SELF_ID = '''- heading "Voluntary Self-Identification of Disability" [level=3] [ref=e1]
- generic [ref=e2]: Form CC-305
- textbox "Name" [ref=e3]
- group "Date" [ref=e4]:
  - generic [ref=e5]: Date*
  - spinbutton "Month" [ref=e6]
  - spinbutton "Day" [ref=e7]
  - spinbutton "Year" [ref=e8]
'''
PERSONAL = '''- heading "My Experience" [level=3] [ref=e1]
- group "Date of birth" [ref=e4]:
  - spinbutton "Month" [ref=e6]
  - spinbutton "Day" [ref=e7]
  - spinbutton "Year" [ref=e8]
'''


def agent():
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    return page_agent.PageAgent(assistant, None, SimpleNamespace(), config.UserProfile(), None,
                                SimpleNamespace(title="Engineer", company="Acme", url="https://acme.example/apply"))


def answers(snapshot):
    a = agent()
    return {c.question: a.known_answer(c) for c in page_agent.parse_snapshot(snapshot) if c.role == "spinbutton"}


def test_the_three_boxes_of_the_date_on_a_self_identification_form_take_todays_date():
    today = date.today()
    got = answers(SELF_ID)
    assert got["Month"] == (f"{today:%m}", "profile.application_date")
    assert got["Day"] == (f"{today:%d}", "profile.application_date")
    assert got["Year"] == (f"{today:%Y}", "profile.application_date")


def test_three_boxes_of_any_other_date_are_still_left_open():
    assert set(answers(PERSONAL).values()) == {("", "")}


def test_a_bare_date_box_on_a_self_identification_form_is_still_todays_date():
    a = agent()
    control = page_agent.Control(ref="e9", role="textbox", name="Date", container="",
                                 context="", section="Voluntary Self-Identification of Disability")
    assert a.known_answer(control) == (date.today().isoformat(), "profile.application_date")
