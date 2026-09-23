"""Unit tests for concept_matcher.py -- Universal Concept Synonym Engine."""

from types import SimpleNamespace
from concept_matcher import (
    clean_text,
    match_concept,
    best_option_match,
    resolve_profile_value,
    STATE_MAP,
)


def test_clean_text():
    assert clean_text("School *") == "school"
    assert clean_text("Position Title (required)") == "position title"
    assert clean_text("State / Province *") == "state province"
    assert clean_text("Years of Experience *") == "years of experience"


def test_concept_matching_job_title_variations():
    variations = [
        "Job Title",
        "Position Title *",
        "Current Position Title",
        "Current Role",
        "Role Title",
        "Designation",
        "Occupation",
        "Current Occupation",
        "Headline",
    ]
    for text in variations:
        concept = match_concept(text)
        assert concept == "CURRENT_JOB_TITLE", f"Failed for {text}, got {concept}"


def test_concept_matching_employer_variations():
    variations = [
        "Employer",
        "Employer Name *",
        "Company Name",
        "Current Employer",
        "Current Company",
        "Organization Name",
        "Current Organization",
        "Workplace",
    ]
    for text in variations:
        concept = match_concept(text)
        assert concept == "CURRENT_EMPLOYER", f"Failed for {text}, got {concept}"


def test_concept_matching_education_variations():
    # School
    for text in ["School *", "University Name", "College Name", "Educational Institution", "Alma Mater"]:
        concept = match_concept(text)
        assert concept == "SCHOOL_UNIVERSITY", f"Failed for {text}, got {concept}"

    # Major
    for text in ["Major", "Field of Study *", "Course of Study", "Specialization", "Degree Subject", "Discipline"]:
        concept = match_concept(text)
        assert concept == "MAJOR_FIELD_OF_STUDY", f"Failed for {text}, got {concept}"

    # Degree
    for text in ["Degree *", "Highest Level of Education", "Educational Attainment", "Highest Degree"]:
        concept = match_concept(text)
        assert concept == "DEGREE_LEVEL", f"Failed for {text}, got {concept}"


def test_concept_matching_personal_contact_variations():
    assert match_concept("First Name *") == "FIRST_NAME"
    assert match_concept("Given Name") == "FIRST_NAME"
    assert match_concept("Last Name *") == "LAST_NAME"
    assert match_concept("Surname") == "LAST_NAME"
    assert match_concept("E-mail Address *") == "EMAIL"
    assert match_concept("Mobile Phone *") == "PHONE_MOBILE"
    assert match_concept("Cell Phone") == "PHONE_MOBILE"
    assert match_concept("Street Address") == "STREET_ADDRESS"
    assert match_concept("City / Town *") == "CITY"
    assert match_concept("State / Province *") == "STATE_PROVINCE"
    assert match_concept("ZIP / Postal Code *") == "POSTAL_CODE"
    assert match_concept("Country of Residence *") == "COUNTRY"


def test_concept_matching_eeo_and_disclosures():
    assert match_concept("Are you legally authorized to work in the United States?") == "WORK_AUTHORIZATION"
    assert match_concept("Will you now or in the future require visa sponsorship?") == "VISA_SPONSORSHIP"
    assert match_concept("Protected Veteran Status") == "VETERAN_STATUS"
    assert match_concept("Disability Status") == "DISABILITY_STATUS"
    assert match_concept("Are you at least 18 years of age?") == "LEGAL_AGE_18"
    assert match_concept("Are you willing to submit to a pre-employment background check?") == "BACKGROUND_CHECK"
    assert match_concept("Are you willing to take a drug test?") == "DRUG_TEST"
    assert match_concept("Are you bound by any non-compete agreement?") == "NON_COMPETE"
    assert match_concept("How did you hear about this opportunity?") == "HOW_DID_YOU_HEAR"


def test_negative_guards():
    # Supervisor should NOT match candidate name or candidate job title
    assert match_concept("Supervisor Name") != "FIRST_NAME"
    assert match_concept("Supervisor Name") != "LAST_NAME"
    assert match_concept("Supervisor Title") != "CURRENT_JOB_TITLE"

    # Emergency contact should not match candidate name/phone
    assert match_concept("Emergency Contact Name") != "FIRST_NAME"
    assert match_concept("Emergency Contact Phone") != "PHONE_MOBILE"

    # High school should not match university
    assert match_concept("High School Name") != "SCHOOL_UNIVERSITY"


def test_container_boost():
    # When a field is simply "Title", container="Experience" resolves to CURRENT_JOB_TITLE
    concept = match_concept("Title", container="Work Experience")
    assert concept == "CURRENT_JOB_TITLE"

    # When a field is simply "School", container="Education" resolves to SCHOOL_UNIVERSITY
    concept = match_concept("Name", container="Education")
    assert concept == "SCHOOL_UNIVERSITY"


def test_best_option_match():
    # State full vs abbreviation
    opts = ["Select One", "VA", "MD", "DC"]
    assert best_option_match("Virginia", opts) == "VA"

    # Country variations
    opts = ["Select Country", "Canada", "United States of America", "Mexico"]
    assert best_option_match("United States", opts) == "United States of America"

    # Exact match
    opts = ["Master's Degree", "Bachelor's Degree", "High School"]
    assert best_option_match("Master's Degree", opts) == "Master's Degree"


def test_resolve_profile_value():
    profile = SimpleNamespace(
        first_name="Yaswanth",
        last_name="Jonnalagadda",
        email="yaswanth@example.com",
        phone_mobile="703-555-0199",
        current_position_title="Senior Network and Security Engineer",
        current_employer="Capital One",
        state="Virginia",
        education=(
            ("Master's", "Computer Technology", "Eastern Illinois University", "2022"),
        ),
        requires_visa_sponsorship=False,
        legally_eligible_to_work="Yes",
    )

    val, src = resolve_profile_value("CURRENT_JOB_TITLE", profile)
    assert val == "Senior Network and Security Engineer"
    assert src == "profile.current_position_title"

    val, src = resolve_profile_value("CURRENT_EMPLOYER", profile)
    assert val == "Capital One"
    assert src == "profile.current_employer"

    val, src = resolve_profile_value("SCHOOL_UNIVERSITY", profile)
    assert val == "Eastern Illinois University"
    assert src == "profile.education.school"

    val, src = resolve_profile_value("MAJOR_FIELD_OF_STUDY", profile)
    assert val == "Computer Technology"
    assert src == "profile.education.major"

    # With state option matching
    val, _ = resolve_profile_value("STATE_PROVINCE", profile, options=["Select One", "VA", "MD", "DC"])
    assert val == "VA"

