"""Praxis Precision Medicines, 25 September: the sponsorship guard disqualified a job whose page asks
"Are you authorized to work in the United States (with or without sponsorship)?".

That is a screening question that accepts either kind of candidate, not an employer refusing to
sponsor. The guard's pattern found "without sponsorship" inside it. The class: a phrase matched without
the words that reverse its meaning ("with or" before "without").

safety.py changes here with the owner's explicit approval (25 September 2026). Wording that really
excludes candidates who need sponsorship keeps blocking.
"""
from types import SimpleNamespace

import pytest

import safety


@pytest.mark.parametrize("text", [
    "Are you authorized to work in the United States (with or without sponsorship)?",
    "Are you legally authorized to work in the U.S. with or without visa sponsorship?",
    "Are you authorized to work in the United States with and/or without sponsorship?",
    "Authorized to work in the US with/or without employment-based visa sponsorship",
    "ARE YOU AUTHORIZED TO WORK IN THE UNITED STATES (WITH OR WITHOUT SPONSORSHIP)?",
    "Candidates may apply with or without the need for sponsorship.",
])
def test_an_inclusive_question_is_not_a_refusal_to_sponsor(text):
    assert safety.no_sponsorship_statement(text) == ""


@pytest.mark.parametrize("text", [
    "This position requires authorization to work in the U.S. without the need for employment-based visa sponsorship.",
    "Are you authorized to work in the United States without sponsorship?",
    "You must be authorized to work in the U.S. without visa sponsorship.",
    "We are unable to sponsor visas for this role.",
    "Sponsorship is not available for this position.",
    "No visa sponsorship will be provided.",
    "U.S. citizens only.",
    "The company will not sponsor an employment visa.",
    "Applicants must be able to work without sponsorship now or in the future.",
])
def test_wording_that_excludes_candidates_who_need_sponsorship_still_blocks(text):
    assert safety.no_sponsorship_statement(text)


def test_an_inclusive_question_beside_a_real_refusal_still_blocks():
    text = ("Are you authorized to work in the United States (with or without sponsorship)? "
            "Please note we are unable to sponsor visas for this role.")
    assert "unable to sponsor" in safety.no_sponsorship_statement(text)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    pg = context.new_page()
    yield pg
    context.close()


def test_the_shield_lets_the_praxis_page_through_and_still_stops_a_real_refusal(page, tmp_path):
    profile = SimpleNamespace(requires_visa_sponsorship=True)
    page.set_content("<h1>Senior Engineer</h1><p>Are you authorized to work in the United States "
                     "(with or without sponsorship)?</p>")
    assert safety.check_visa_sponsorship_shield(page, profile, job_dir=tmp_path)[0] is False
    page.set_content("<h1>Senior Engineer</h1><p>Are you authorized to work in the United States "
                     "without sponsorship?</p>")
    assert safety.check_visa_sponsorship_shield(page, profile, job_dir=tmp_path)[0] is True
