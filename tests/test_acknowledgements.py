"""Tests for Task 3: Always accept acknowledgements, consents, certifications, and marketing opt-ins.

Rules under test:
1. reference/acknowledgements.json contains phrases for terms, privacy, data processing,
   "I certify / attest the information is true", background check consent, e-signature,
   at-will, arbitration, SMS/email contact consent, and marketing opt-ins.
2. A checkbox or button whose label matches is ticked or pressed. A pre-ticked box stays ticked.
3. _account_acknowledgment_is_authorized returns True for every matching label (no longer profile-gated).
4. marketing_opt_in: keep the detector, but its result now means 'tick'.
5. Exception that stays: a checkbox that is itself a legal answer (e.g. "I am authorized to work in the US",
   "I require sponsorship") is a question, not an acknowledgement, and is never ticked blindly.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from config import UserProfile
from page_agent import Control, PageAgent


def _make_agent(profile: UserProfile | None = None) -> PageAgent:
    agent = PageAgent.__new__(PageAgent)
    agent.profile = profile or UserProfile()
    agent._profile_answer_library = None
    agent._entries = {}
    agent.history = {}
    agent.notes = []
    return agent


def test_acknowledgements_json_exists_and_contains_required_categories():
    """reference/acknowledgements.json exists and defines all required categories."""
    path = Path("reference/acknowledgements.json")
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "categories" in data
    cats = data["categories"]
    for req in [
        "terms",
        "privacy",
        "data_processing",
        "certifications",
        "background_check",
        "e_signature",
        "at_will",
        "arbitration",
        "contact_consent",
        "marketing_opt_ins",
    ]:
        assert req in cats, f"Missing category {req} in reference/acknowledgements.json"


def test_is_acknowledgement_matches_required_phrases():
    """acknowledgements.is_acknowledgement identifies each class of consent."""
    import acknowledgements

    assert acknowledgements.is_acknowledgement("I agree to the Terms and Conditions")
    assert acknowledgements.is_acknowledgement("I have read and accept the Privacy Policy")
    assert acknowledgements.is_acknowledgement("Consent to data processing in accordance with GDPR")
    assert acknowledgements.is_acknowledgement("I certify that all information submitted is true and complete")
    assert acknowledgements.is_acknowledgement("I authorize the company to conduct a background check")
    assert acknowledgements.is_acknowledgement("I agree to sign this application with an electronic signature")
    assert acknowledgements.is_acknowledgement("I understand that employment is at-will")
    assert acknowledgements.is_acknowledgement("Agreement to mutual binding arbitration")
    assert acknowledgements.is_acknowledgement("I consent to receive automated text messages regarding my application")
    assert acknowledgements.is_acknowledgement("Send me emails about new arrivals, hot items, daily savings and more.")


def test_legal_question_checkbox_is_not_an_acknowledgement():
    """Legal questions like work authorization and sponsorship must NOT be auto-ticked as acknowledgements."""
    import acknowledgements

    assert not acknowledgements.is_acknowledgement("I require visa sponsorship now or in the future")
    assert not acknowledgements.is_acknowledgement("Are you authorized to work in the United States?")
    assert not acknowledgements.is_acknowledgement("Will you now or in the future require sponsorship for employment visa status?")
    assert not acknowledgements.is_acknowledgement("I am authorized to work in the US")


def test_account_acknowledgment_is_authorized_without_profile_gate():
    """_account_acknowledgment_is_authorized returns True for matching labels without sign_attestations."""
    from browser_automation import JobApplicationAssistant

    profile_no_attestations = UserProfile(sign_attestations=False)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)

    # All acknowledgement labels are authorized even when sign_attestations is False
    assert assistant._account_acknowledgment_is_authorized("I agree to the Terms of Service", profile_no_attestations)
    assert assistant._account_acknowledgment_is_authorized("I certify that the information is true and correct", profile_no_attestations)
    assert assistant._account_acknowledgment_is_authorized("I consent to a pre-employment background investigation", profile_no_attestations)
    assert assistant._account_acknowledgment_is_authorized("Send me emails about new arrivals, hot items, daily savings and more.", profile_no_attestations)

    # Legal question is not authorized as an acknowledgement
    assert not assistant._account_acknowledgment_is_authorized("I require visa sponsorship", profile_no_attestations)


def test_walmart_preticked_marketing_box_stays_ticked():
    """Walmart's pre-ticked marketing box stays ticked (f276 rule reversal)."""
    from browser_automation import JobApplicationAssistant

    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    page = MagicMock()

    # _clear_site_ticked_marketing must no longer untick marketing boxes
    stuck = assistant._clear_site_ticked_marketing(page)
    assert stuck == []


def test_page_agent_known_answer_ticks_terms_and_background_check():
    """PageAgent.known_answer returns ('checked', source) for terms and background check."""
    agent = _make_agent(UserProfile(sign_attestations=False, accept_application_privacy_prompts=False))

    terms_ctrl = Control(ref="c1", role="checkbox", name="I agree to the Terms and Conditions")
    ans, source = agent.known_answer(terms_ctrl)
    assert ans == "checked"

    bg_ctrl = Control(ref="c2", role="checkbox", name="I authorize a background check")
    ans_bg, source_bg = agent.known_answer(bg_ctrl)
    assert ans_bg == "checked"


def test_page_agent_known_answer_does_not_auto_tick_sponsorship():
    """PageAgent.known_answer does NOT auto-tick a sponsorship or authorization checkbox."""
    agent = _make_agent(UserProfile(sign_attestations=False, requires_visa_sponsorship=False))

    sponsor_ctrl = Control(ref="c3", role="checkbox", name="I require sponsorship for employment visa status")
    ans, source = agent.known_answer(sponsor_ctrl)
    # Must NOT be auto-checked as an acknowledgement
    assert ans != "checked"


def test_job_conditions_are_not_acknowledgements():
    """Job conditions (on-site, relocate, travel, salary, shift, etc.) are questions, not acknowledgements."""
    import acknowledgements

    assert not acknowledgements.is_acknowledgement("I accept that this role is 100% on-site")
    assert not acknowledgements.is_acknowledgement("I agree to relocate")
    assert not acknowledgements.is_acknowledgement("I agree to travel up to 50%")
    assert not acknowledgements.is_acknowledgement("I accept the shift hours and weekend schedule")
    assert acknowledgements.is_acknowledgement("I have read and agree to the privacy policy")


def test_a_routine_certify_information_is_true_acknowledgement_is_still_ticked():
    """"I certify that... is complete and true" is a routine certification almost every application has,
    and is already required to tick (test_acknowledgements_json_exists_and_contains_required_categories /
    Task 3) -- not a "signature equivalent" or "under penalty of perjury" declaration that needs
    sign_attestations. The new _CANDIDATE_SIGNATURE_DECLARATION_RE guard (Task 6) used to catch this
    phrasing too and blank it; replay_guard.py caught the regression on a real saved Pacific Life page."""
    agent = _make_agent(UserProfile(sign_attestations=False, accept_application_privacy_prompts=False))
    ctrl = Control(ref="c7", role="checkbox",
                   name="I certify that all information provided by me on this application is complete "
                        "and true to the best of my knowledge and belief.")
    ans, _source = agent.known_answer(ctrl)
    assert ans == "checked"


def test_a_checkbox_named_only_by_its_container_is_still_read():
    """A checkbox whose own accessible name is empty, with its real label sitting in a sibling/container
    element instead (context: "nearby text, when the control has no name of its own") -- the ARIA shape
    several real ATS forms use even for a standard "I agree to the Terms and Conditions" box -- used to
    be blanked outright (`if not control.name: return "", ""`) before that context was ever read;
    replay_guard.py caught the regression on a real saved Church Mutual page."""
    agent = _make_agent(UserProfile(sign_attestations=False, accept_application_privacy_prompts=False))
    ctrl = Control(ref="c8", role="checkbox", name="", context="I agree to the Terms and Conditions")
    ans, _source = agent.known_answer(ctrl)
    assert ans == "checked"


def test_a_real_perjury_signature_declaration_is_still_left_for_the_owner():
    """The new guard's actual target -- a declaration that explicitly invokes legal signature-equivalence
    or perjury -- must still be left alone without sign_attestations; narrowing the regex to fix the two
    cases above must not neuter the feature it was added for."""
    agent = _make_agent(UserProfile(sign_attestations=False))
    ctrl = Control(ref="c9", role="checkbox",
                   name="I certify under penalty of perjury that the foregoing statements are true.")
    ans, _source = agent.known_answer(ctrl)
    assert ans != "checked"
