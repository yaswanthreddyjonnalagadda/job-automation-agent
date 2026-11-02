"""Tests for P0-3: Dropdown pre-opening and concept option resolution.

Verifies:
1. Closed comboboxes with options=() in initial ARIA snapshot have choices pre-read
   before concept matching and profile value resolution.
2. Option-sensitive concepts (WORK_AUTHORIZATION, ETHNICITY_RACE, RESIDENCE) resolve
   to the exact options offered by the site rather than uncalibrated fallback strings.
3. ADP custom widget interactions (sdf-select-simple, sdf-card, sdf-focus-pane).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest

import config
import concept_matcher
import option_match
import page_agent
from page_agent import Control, Answer, PageAgent
from sites.adp import AdpAdapter


def test_concept_resolution_with_sponsorship_options():
    profile = config.UserProfile(
        authorized_for_any_employer="Yes",
        requires_visa_sponsorship=True
    )
    # Options offered by modern ATS forms (Workday / ADP)
    site_options = [
        "Authorized to work for any employer without restriction",
        "Authorized to work for current employer with sponsorship",
        "Not authorized to work in the United States"
    ]

    # Without options: resolves to bare "Yes"
    val_empty, _ = concept_matcher.resolve_profile_value("WORK_AUTHORIZATION", profile, options=())
    assert val_empty == "Yes"
    # But option_match.best_option fails to match "Yes" against site options
    assert option_match.best_option(site_options, val_empty) is None

    # WITH options (P0-3): resolves to the calibrated site option
    val_calibrated, src = concept_matcher.resolve_profile_value("WORK_AUTHORIZATION", profile, options=site_options)
    assert val_calibrated == "Authorized to work for current employer with sponsorship"
    assert option_match.best_option(site_options, val_calibrated) == 1


def test_ethnicity_concept_confirmed_to_hispanic_when_options_known():
    profile = config.UserProfile(ethnicity="Asian", hispanic_or_latino="No")
    hispanic_options = ["Hispanic or Latino", "Not Hispanic or Latino"]

    # Without options: stays ETHNICITY_RACE, resolves to "Asian", which fails to match
    concept_empty = concept_matcher.confirm_concept("ETHNICITY_RACE", ())
    assert concept_empty == "ETHNICITY_RACE"
    val_empty, _ = concept_matcher.resolve_profile_value(concept_empty, profile, options=())
    assert val_empty == "Asian"
    assert option_match.best_option(hispanic_options, val_empty) is None

    # WITH options (P0-3): confirm_concept redirects to HISPANIC_OR_LATINO
    concept_calibrated = concept_matcher.confirm_concept("ETHNICITY_RACE", hispanic_options)
    assert concept_calibrated == "HISPANIC_OR_LATINO"
    val_calibrated, _ = concept_matcher.resolve_profile_value(concept_calibrated, profile, options=hispanic_options)
    assert val_calibrated == "Not Hispanic or Latino"
    assert option_match.best_option(hispanic_options, val_calibrated) == 1


def test_page_agent_answer_what_is_known_prereads_closed_dropdown(monkeypatch):
    """When a combobox has options=(), answer_what_is_known pre-reads choices before resolving."""
    profile = config.UserProfile(
        authorized_for_any_employer="Yes",
        requires_visa_sponsorship=True
    )
    agent = PageAgent(
        SimpleNamespace(_best_option=option_match.best_option),
        None,  # claude
        SimpleNamespace(ats_email=""),
        profile,
        SimpleNamespace(raw_text=""),
        SimpleNamespace(title="Network Engineer", company="Example Corp", url="https://example.com"),
        resume_file=None
    )
    agent._current_host = "example.com"
    agent._published_questions = lambda: {}

    mock_choices = [
        "Authorized to work for any employer without restriction",
        "Authorized to work for current employer with sponsorship",
        "Not authorized to work in the United States"
    ]
    # Mock _open_and_read to simulate opening the dropdown and capturing options
    monkeypatch.setattr(agent, "_open_and_read", lambda page, control, q: mock_choices)
    monkeypatch.setattr(agent, "do", lambda page, answer, control: True)

    control = Control(
        ref="c1",
        role="combobox",
        name="Are you legally authorized to work in the United States?",
        options=()  # Initial snapshot has no options
    )
    monkeypatch.setattr(agent, "_page_controls", lambda p: [control])

    mock_page = SimpleNamespace()
    filled, open_q = agent.answer_what_is_known(mock_page, [control], required=[])

    # Verified: choices were pre-read and populated onto control.options
    assert control.options == tuple(mock_choices)
    # The written answer used the exact calibrated string, not bare "Yes"
    assert agent.written[control.question] == "Authorized to work for current employer with sponsorship"
    assert filled == 1
    assert open_q == []


def test_adp_adapter_custom_widgets():
    adapter = AdpAdapter()
    assert adapter.matches("https://myjobs.adp.com/company/job-details?reqId=123") is True
    assert "sdf-focus-pane" in adapter.overlay_selector()

    # Mock page for ADP widget selectors matching Playwright Locator protocol
    class MockLocator:
        def __init__(self, count_val=1, text="Yes"):
            self._count = count_val
            self._text = text
            self.clicked = False

        def count(self):
            return self._count

        def scroll_into_view_if_needed(self, timeout=0):
            pass

        def click(self, timeout=0):
            self.clicked = True

        def nth(self, i):
            return self

        def inner_text(self, timeout=0):
            return self._text

        def filter(self, **kwargs):
            return self

        def locator(self, selector):
            return self

        @property
        def first(self):
            return self

    class MockADPPage:
        def locator(self, selector):
            return MockLocator(count_val=1, text="Yes")

    mock_page = MockADPPage()
    assert adapter.open_picker(mock_page, {"selector": "sdf-select-simple"}) is True
    assert adapter.select_option(mock_page, "sdf-select-simple", "Yes") is True
    assert adapter.commit_card(mock_page, MockLocator(count_val=1)) is True

