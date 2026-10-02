"""The last page's Submit is never taken for a mid-form one.

Aristocrat (Workday), 29 September: the Review page's progress bar read "completed step 1 of 5 ... completed
step 4 of 5, current step 5 of 5". The page was planned from the profile alone, which took the first
"step N of M" -- "1 of 5" -- for the current step, called the final "Submit" a mid-form one, and pressed it.
The application was sent without the owner's review.
"""
from types import SimpleNamespace

import pytest

import config
from page_agent import Control, PageAgent, current_step

WORKDAY_REVIEW = """- list "Application progress":
  - listitem: completed step 1 of 5 My Information
  - listitem: completed step 2 of 5 My Experience
  - listitem: completed step 3 of 5 Application Questions
  - listitem: completed step 4 of 5 Voluntary Disclosures
  - listitem: current step 5 of 5 Review
- heading "Review" [level=2]
- button "Submit" [ref=e9]"""


@pytest.mark.parametrize("snapshot, expected", [
    (WORKDAY_REVIEW, (5, 5)),
    (WORKDAY_REVIEW.replace("current step 5 of 5", "current step 2 of 5"), (2, 5)),   # Schwab-like mid step
    ("- heading \"Step 2 of 5\"", (2, 5)),                                            # a plain counter
    ("- text: Page 3 / 4\n- text: page 3 of 4", (3, 4)),                               # the same one, twice
    ("- text: step 1 of 5\n- text: step 4 of 5", None),                                # which one? unknown
    ("- text: completed step 1 of 5", None),                                          # only finished steps
    ("- heading \"Application\"", None),
])
def test_the_step_the_page_is_on(snapshot, expected):
    assert current_step(snapshot) == expected


def agent():
    job = SimpleNamespace(title="Engineer", company="Acme", url="https://jobs.example.com/apply/1")
    return PageAgent(SimpleNamespace(values=None), SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
                     config.UserProfile(email="jane@example.com"), SimpleNamespace(raw_text="x"), job, resume_file=None)


def test_the_submit_on_workdays_review_page_is_the_final_one():
    plan = agent().profile_plan([Control(ref="e9", role="button", name="Submit")], set(), [], WORKDAY_REVIEW)
    assert plan.next_kind == "final_submit" and plan.step == "5 of 5"


def test_a_submit_on_a_step_with_more_to_come_still_only_saves_that_step():
    mid = WORKDAY_REVIEW.replace("current step 5 of 5", "current step 2 of 5")
    plan = agent().profile_plan([Control(ref="e9", role="button", name="Submit")], set(), [], mid)
    assert plan.next_kind == "next_step" and plan.step == "2 of 5"


def test_a_submit_whose_step_cannot_be_told_is_treated_as_the_last():
    muddled = "- text: step 1 of 5\n- text: step 4 of 5\n- button \"Submit\" [ref=e9]"
    plan = agent().profile_plan([Control(ref="e9", role="button", name="Submit")], set(), [], muddled)
    assert plan.next_kind == "final_submit"
