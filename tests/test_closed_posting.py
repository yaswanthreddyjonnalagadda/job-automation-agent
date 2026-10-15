import pytest
from page_agent import closed_posting_message

@pytest.mark.parametrize("text,expected", [
    ("About the Job\nThe job position is no longer available.\nExplore other jobs", True),
    ("This job is no longer available", True),
    ("First name\nThis option is no longer available", False),
    ("FAQs\nThe job position is no longer available.", False),
    ("You can contact us if this job is no longer available.", False),
    ("Thank you for applying!", False),
])
def test_closed_posting_requires_explicit_notice(text, expected):
    assert bool(closed_posting_message(text)) is expected
