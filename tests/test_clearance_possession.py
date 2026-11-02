from types import SimpleNamespace
import pytest
import config
from page_agent import PageAgent, Control
from browser_automation import JobApplicationAssistant

@pytest.mark.parametrize("question,expected", [
    ("Do you have a current/active security clearance? *", "No"),
    ("The position requires current/active TS/SCI clearance. Do you currently possess the required clearance for this opportunity? *", "No"),
    ("Have you ever held a security clearance?", ""),
    ("Have you held a security clearance?", ""),
    ("Did you have a security clearance?", ""),
    ("Have you previously possessed a security clearance?", ""),
    ("Do you have any objection to obtaining a security clearance?", ""),
    ("Are you willing to obtain a security clearance?", ""),
])
def test_no_current_clearance_is_only_a_possession_answer(question, expected):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    agent = PageAgent(assistant,None,SimpleNamespace(),config.UserProfile(security_clearance="No"),None,
                      SimpleNamespace(title="Engineer",company="Example",url="https://example.com"))
    agent.library_answer = lambda *a, **kw: ''
    value, _ = agent.known_answer(Control("field", "combobox", question, options=["Yes", "No"]))
    assert value == expected
