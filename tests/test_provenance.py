"""Who put a value on the form (RFC-001, M1).

The agent used to know only its own writes and treated everything else as
the owner's answer, so a value the site put there could not be corrected --
which is how "Afghanistan" ended up hard-coded as an exception. These tests
pin the replacement: a person's input is observed, the site's is not
mistaken for it, and neither the owner's answer nor the owner's policy is
ever overruled.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from playwright.sync_api import sync_playwright

import provenance
import safety

FORM = """<label for=country>Country</label><input id=country value="Afghanistan">
<label for=city>City</label><input id=city value="">"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def observed(browser):
    """A page watched by the provenance observer, as every agent page is."""
    context = browser.new_context()
    provenance.install(context)
    page = context.new_page()
    page.set_content(FORM)
    provenance.install_on_page(page)
    yield page
    context.close()


@pytest.fixture
def unobserved(browser):
    context = browser.new_context()
    page = context.new_page()
    page.set_content(FORM)
    yield page
    context.close()


def test_a_value_nobody_touched_is_the_sites(observed):
    values = safety.AgentValues()
    assert values.origin(observed, "#country", "Afghanistan") == provenance.SITE


def test_what_the_agent_types_is_never_taken_for_the_owner(observed):
    observed.fill("#city", "Fairfax")            # the page starts every document "agent busy"
    assert provenance.owner_edited(observed, "#city") is False


def test_what_a_person_types_while_the_agent_waits_is_theirs(observed):
    values = safety.AgentValues()
    provenance.set_agent_busy(observed, False)    # the owner's turn
    observed.fill("#country", "Afghanistan")       # a person really chose it
    provenance.set_agent_busy(observed, True)
    assert values.origin(observed, "#country", "Afghanistan") == provenance.OWNER
    assert values.may_correct(observed, "#country", "Afghanistan") is False


def test_the_sites_value_may_be_corrected_unless_the_owner_said_leave_it(observed, monkeypatch):
    values = safety.AgentValues()
    monkeypatch.setenv("SITE_PREFILL_POLICY", "correct")
    assert values.may_correct(observed, "#country", "Afghanistan") is True
    monkeypatch.setenv("SITE_PREFILL_POLICY", "leave")
    assert values.may_correct(observed, "#country", "Afghanistan") is False


def test_without_the_observer_a_present_value_is_left_alone(unobserved):
    values = safety.AgentValues()
    assert values.origin(unobserved, "#country", "Afghanistan") == provenance.UNKNOWN
    assert values.may_correct(unobserved, "#country", "Afghanistan") is False


def test_the_agents_own_answer_may_always_be_rewritten(observed):
    values = safety.AgentValues()
    observed.fill("#city", "Fairfx")
    values.record(observed, "#city", "Fairfx", "profile:city")
    assert values.may_write(observed, "#city", "Fairfx")
    assert values.may_correct(observed, "#city", "Fairfx")


@pytest.mark.parametrize("origin,policy,allowed", [
    (provenance.EMPTY, "leave", True),
    (provenance.AGENT, "leave", True),       # the agent may put its own answer right
    (provenance.SITE, "correct", True),
    (provenance.SITE, "leave", False),
    (provenance.OWNER, "correct", False),    # never, whatever the policy
    (provenance.UNKNOWN, "correct", False),  # cannot tell the site from the owner
])
def test_one_rule_decides_who_may_be_overruled(origin, policy, allowed):
    assert safety.may_overrule(origin, policy) is allowed


def test_no_other_module_decides_it():
    """One decision, one place (RFC-001): the policy's value is read only in
    safety.may_overrule, so an exception cannot be pasted into four modules again."""
    import re

    root = Path(__file__).resolve().parents[1]
    decides = re.compile(r"""site_prefill_policy\(\)\s*[!=]=|policy\s*[!=]=\s*["'](correct|leave)["']""")
    offenders = [p.name for p in sorted(root.glob("*.py")) + sorted((root / "sites").glob("*.py"))
                 if p.name not in ("safety.py", "config.py") and decides.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_the_observer_survives_the_page_being_rewritten(observed):
    """document.open() -- which set_content uses -- erases listeners."""
    observed.set_content(FORM)
    provenance.install_on_page(observed)
    provenance.set_agent_busy(observed, False)
    observed.fill("#city", "Arlington")
    assert provenance.owner_edited(observed, "#city") is True


def test_only_what_a_person_entered_is_learned_as_their_answer(observed):
    import apply_flow
    from browser_automation import JobApplicationAssistant

    observed.set_content('<label for=a>Preferred work location</label><input id=a value="Kabul">'
                         '<label for=b>Preferred start date</label><input id=b>')
    provenance.install_on_page(observed)
    provenance.set_agent_busy(observed, False)
    observed.fill("#b", "In two weeks")          # the owner answers one question
    provenance.set_agent_busy(observed, True)

    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    remembered = []
    tracker = SimpleNamespace(record_answer=lambda key, host, label, value, answered_by: remembered.append(
        (label, value, answered_by)))
    profile = SimpleNamespace()
    apply_flow.learn_user_answers(assistant, observed, tracker, "k", profile)
    assert ("Preferred start date", "In two weeks", "user") in remembered
    assert all(label != "Preferred work location" for label, _v, _b in remembered)   # the site's, not theirs
