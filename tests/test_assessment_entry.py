"""Explicit mandatory assessment entries precede generic Next buttons."""
import pytest
from page_agent import PageAgent, Control

@pytest.mark.parametrize("role", ["button", "link"])
@pytest.mark.parametrize("body,expected", [
    ("You must complete the assessment test to continue.", "assessment"),
    ("You must complete an assessment to continue.", "assessment"),
    ("You have completed the assessment.", "next"),
    ("Step 5 Take Assessment. Review your application.", "next"),
])
def test_required_assessment_entry(role, body, expected):
    agent = PageAgent.__new__(PageAgent)
    controls = [Control("next", "button", "Continue"), Control("assessment", role, "Take Assessment")]
    assert agent.profile_forward(controls, body).ref == expected
