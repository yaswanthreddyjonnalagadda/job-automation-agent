"""
Regression tests for SmartRecruiters, iCIMS, Workday verification, and option range hardening (f189).

Covers:
1. SmartRecruiters guest apply: 'Confirm your email*' beside First/Last name does not trigger
   VERIFY_EMAIL (remains NONE).
2. Workday verification notification on sign-in page correctly triggers VERIFY_EMAIL.
3. Option matching for numeric salary/compensation buckets and years of experience.
4. File upload buttons ('Choose file', 'Upload file') are never treated as generic dropdowns.
5. iCIMS adapter registration, URL matching, and #icims_content_iframe targeting.
6. ATS vendor URL path company resolution in job_sources (Versant instead of SMARTRECRUITERS).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import account_state
import job_sources
import option_match
import page_agent
from sites import adapter_for
from sites.icims import IcimsAdapter


def test_smartrecruiters_guest_apply_not_misclassified_as_verify_email():
    snapshot = """
    - text: "Apply for Support Engineer"
    - textbox "First name*" [ref=e1]:
    - textbox "Last name*" [ref=e2]:
    - textbox "Email*" [ref=e3]:
    - textbox "Confirm your email*" [ref=e4]:
    - textbox "Phone*" [ref=e5]:
    - button "Next" [ref=e6]:
    """
    state = account_state.read_state(snapshot)
    assert state.kind == account_state.NONE, f"Expected NONE but got {state.kind} ({state.why})"


def test_workday_account_creation_verification_notification_classified_as_verify_email():
    snapshot = """
    - heading "Sign In"
    - text: "We have sent a verification email to your address. Please verify your email before signing in."
    - textbox "Username" [ref=e1]:
    - textbox "Password" [ref=e2]:
    - button "Sign In" [ref=e3]:
    """
    state = account_state.read_state(snapshot)
    assert state.kind == account_state.VERIFY_EMAIL, f"Expected VERIFY_EMAIL but got {state.kind}"


def test_numeric_salary_range_bucket_option_matching():
    options = [
        "Select One",
        "Below $20,000/yr",
        "$20,000 - $30,000/yr",
        "$101,000 - $110,000/yr",
        "$111,000 - $120,000/yr",
        "$121,000 - $130,000/yr",
        "$300,000+/yr",
    ]
    # Exact boundary match
    assert option_match.best_option(options, "120000") == 4  # "$111,000 - $120,000/yr"
    assert option_match.best_option(options, "$120,000") == 4
    # Inside bucket
    assert option_match.best_option(options, "125000") == 5  # "$121,000 - $130,000/yr"
    assert option_match.best_option(options, "$125,000/yr") == 5
    # Upper/lower bounds
    assert option_match.best_option(options, "15000") == 1   # "Below $20,000/yr"
    assert option_match.best_option(options, "$350,000") == 6  # "$300,000+/yr"
    # Non-numeric should not match
    assert option_match.best_option(options, "Python 3") is None
    assert option_match.best_option(options, "Senior Engineer") is None


def test_years_of_experience_bucket_matching():
    options = ["0 - 1 years", "1 - 3 years", "3 - 5 years", "5+ years"]
    assert option_match.best_option(options, "2") == 1  # "1 - 3 years"
    assert option_match.best_option(options, "4") == 2  # "3 - 5 years"
    assert option_match.best_option(options, "8") == 3  # "5+ years"


def test_file_upload_buttons_excluded_from_generic_dropdown_names():
    upload_names = ["Choose file", "upload file", "browse", "attach file", "Select file"]
    for name in upload_names:
        assert not page_agent.Control.GENERIC_DROPDOWN_NAMES.match(name), f"{name} should not match dropdowns"

    dropdown_names = ["Select one", "select one required", "Choose one"]
    for name in dropdown_names:
        assert page_agent.Control.GENERIC_DROPDOWN_NAMES.match(name), f"{name} should match dropdowns"


def test_icims_adapter_registration_and_frame_targeting():
    url = "https://jobs-mutualofenumclaw.icims.com/jobs/2424/network-engineer/login#icims_content_iframe"
    adapter = adapter_for(url)
    assert isinstance(adapter, IcimsAdapter)
    assert adapter.name == "icims"

    # Frame targeting
    mock_frame = SimpleNamespace(name="icims_content_iframe", url="https://jobs-mutualofenumclaw.icims.com/iframe")
    mock_page = SimpleNamespace(frames=[mock_frame], main_frame=SimpleNamespace(name="main"))
    targeted = adapter.find_application_frame(mock_page)
    assert targeted is mock_frame


def test_job_sources_company_extraction_from_smartrecruiters_url():
    url = "https://jobs.smartrecruiters.com/Versant/744000088915091-support-engineer-automation-cloud"
    company = job_sources._company_from_url(url)
    assert company == "Versant"

