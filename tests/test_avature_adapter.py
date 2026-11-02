"""Tests for AvatureAdapter and Sunday referral unwrapping (P1-5 / f217)."""

import pytest
from sites import adapter_for
from sites.avature import AvatureAdapter
from sites.greenhouse import GreenhouseAdapter


def test_avature_url_matching():
    # Direct Avature tenant hosts
    assert isinstance(adapter_for("https://mantech.avature.net/careers/JobDetail/Senior-Network-Engineer/12345"), AvatureAdapter)
    assert isinstance(adapter_for("https://bloomberg.avature.net/careers/ApplicationMethods?jobId=999"), AvatureAdapter)
    assert isinstance(adapter_for("https://company.avature.cn/en_US/careers/JobDetail/100"), AvatureAdapter)

    # Employer custom domain with Avature path conventions
    assert isinstance(adapter_for("https://careers.mantech.com/careers/JobDetail/12345"), AvatureAdapter)
    assert isinstance(adapter_for("https://careers.example.com/careers/ApplicationMethods?jobId=555"), AvatureAdapter)

    # Sunday referral wrapper
    assert isinstance(adapter_for("https://sundayy.com/jobs/network-engineer-123"), AvatureAdapter)
    assert isinstance(adapter_for("https://referral.sundayy.com/apply/abc"), AvatureAdapter)

    # Unrelated ATS stays with its own adapter
    assert isinstance(adapter_for("https://boards.greenhouse.io/datadog/jobs/123"), GreenhouseAdapter)


def test_referral_wrapper_detection():
    assert AvatureAdapter.is_referral_wrapper("https://sundayy.com/jobs/network-engineer") is True
    assert AvatureAdapter.is_referral_wrapper("https://referral.sundayy.com/track/123") is True
    assert AvatureAdapter.is_referral_wrapper("https://mantech.avature.net/careers/JobDetail/123") is False
    assert AvatureAdapter.is_referral_wrapper("https://boards.greenhouse.io/datadog/jobs/123") is False


def test_unwrap_referral_destination():
    html_with_avature = """
    <div class="apply-section">
        <a class="btn-apply" href="https://mantech.avature.net/careers/JobDetail/Senior-Network-Engineer/12345">Apply on Company Site</a>
    </div>
    """
    dest = AvatureAdapter.unwrap_referral_destination(html_with_avature)
    assert dest == "https://mantech.avature.net/careers/JobDetail/Senior-Network-Engineer/12345"

    html_with_greenhouse = """
    <div>
        <p>Referral opportunity</p>
        <a href="https://boards.greenhouse.io/cloudflare/jobs/987654">Apply Now</a>
    </div>
    """
    dest_gh = AvatureAdapter.unwrap_referral_destination(html_with_greenhouse)
    assert dest_gh == "https://boards.greenhouse.io/cloudflare/jobs/987654"

    # No ATS link found
    assert AvatureAdapter.unwrap_referral_destination("<div><p>Contact recruiter</p></div>") is None


def test_has_resume_entry_alternatives_f161():
    # Avature resume alternatives allow bypassing optional login
    snapshot_with_alternatives = (
        '- heading "Already registered? Login"\n'
        '- paragraph "Choose any option to apply"\n'
        '- button "From Device"\n'
        '- button "Copy & Paste"\n'
        '- button "Without Resume"\n'
    )
    assert AvatureAdapter.has_resume_entry_alternatives(snapshot_with_alternatives) is True

    # Standard form without explicit alternatives requires login
    snapshot_standard = (
        '- heading "Already registered? Login"\n'
        '- button "Upload Resume"\n'
    )
    assert AvatureAdapter.has_resume_entry_alternatives(snapshot_standard) is False


def test_is_closed_posting_f163():
    # Explicit closed notices
    assert AvatureAdapter.is_closed_posting("About the Job\nThe job position is no longer available.\nExplore other jobs") is True
    assert AvatureAdapter.is_closed_posting("This job is no longer available") is True
    assert AvatureAdapter.is_closed_posting("Notice: This position is no longer open") is True

    # Negative / non-closed text
    assert AvatureAdapter.is_closed_posting("First name\nThis option is no longer available") is False
    assert AvatureAdapter.is_closed_posting("FAQs\nThe job position is no longer available.") is False
    assert AvatureAdapter.is_closed_posting("You can contact us if this job is no longer available.") is False
    assert AvatureAdapter.is_closed_posting("Thank you for applying!") is False


def test_format_phone_number_f165():
    # US phone with XXXXXXXXXX placeholder is stripped to 10 plain digits
    assert AvatureAdapter.format_phone_number(
        "(202) 555-0123", "XXXXXXXXXX (use this format for USA phone numbers)", "United States"
    ) == "2025550123"

    assert AvatureAdapter.format_phone_number(
        "+1 202 555 0123", "XXXXXXXXXX", "USA"
    ) == "2025550123"

    # International phone is strictly preserved
    assert AvatureAdapter.format_phone_number(
        "+44 20 7946 0123", "XXXXXXXXXX", "United Kingdom"
    ) == "+44 20 7946 0123"

    # Non-matching placeholder is preserved
    assert AvatureAdapter.format_phone_number(
        "(202) 555-0123", "Phone Number", "United States"
    ) == "(202) 555-0123"

