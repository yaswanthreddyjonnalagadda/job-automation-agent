"""The profile-first answer engine answers whole families of questions from evidence, and says nothing without it (f230).

Every case here uses a synthetic person; nothing depends on the owner's own profile. Several of them are mistakes the
engine made while it was being built and checked against 340 real questions read from public job boards: a degree
question that also asked for ten years answered Yes, a free-text Employee ID answered "Yes", "where can we learn more
about you" taken for "how did you hear", and "Elastic Observability or Security experience" proved by one word.
"""
import pytest

import answer_engine as ae
import config

RESUME = """Senior Network Engineer, Acme Corp, 2019 - present. Designed BGP and OSPF routing, MPLS, SD-WAN and firewall
policy. Automation with Python and Terraform. Led root cause analysis for network incidents. AWS Direct Connect."""

OWNER = config.UserProfile(
    first_name="Pat", last_name="Doe", preferred_name="Pat", current_employer="Acme Corp", city="Fairfax", state="Virginia",
    country="United States", current_location="Fairfax, VA", languages=("English", "Telugu"), years_experience="6",
    legally_eligible_to_work="Yes", country_of_citizenship="India", how_did_you_hear="LinkedIn",
    preferred_contact_method="Email", relatives_employed_here="No", outside_business_interests_with_competitors="No",
    willing_to_work_onsite_three_days="Yes", work_arrangements=("In Person Office", "Remote"), security_clearance="No",
    marketing_opt_in="No", accept_application_privacy_prompts=True, sign_attestations=True, education=(("Master's", "Computer Technology", "Some University", "2019"),),
    available_work_locations="Atlanta, New York, Seattle", locations=("Remote", "Fairfax"),
)


def ask(question, options=(), role="textbox", company="Globex", employers=("Acme Corp",), profile=OWNER, resume=RESUME):
    return ae.Ask(question=question, options=list(options), role=role, profile=profile, company=company,
                  resume_text=resume, employers=list(employers))


def said(question, options=(), role=None, **kw):
    role = role or ("combobox" if options else "textbox")
    return ae.answer(ask(question, options, role, **kw))[0]


YN = ["Yes", "No"]


@pytest.mark.parametrize("question, expected", [
    ("Are you fluent in German or French?", "No"),
    ("Are you fluent in both English and Portuguese?", "No"),
    ("Do you possess business fluency in Japanese?", "No"),
    ("Do you have an advanced english level (writing, listening, speaking)?", "Yes"),
    ("Are you proficient in Telugu or Hindi?", "Yes"),
])
def test_language_questions_follow_the_languages_the_owner_has(question, expected):
    assert said(question, YN) == expected


@pytest.mark.parametrize("question, expected", [
    ("Are you legally entitled to work in Canada?", "No"),
    ("Are you authorized to work in Ireland?", "No"),
    ("Are you legally authorized to work in the United States?", "Yes"),
    ("Are you Singaporean or a Permanent Resident of Singapore?", "No"),
])
def test_eligibility_in_a_named_country_follows_where_the_owner_is_authorized(question, expected):
    assert said(question, YN) == expected


@pytest.mark.parametrize("question, expected", [
    ("Do you have at least 2 years of full time relevant working experience?", "Yes"),
    ("Do you have 4+ years of BGP and OSPF experience?", "Yes"),
    ("Do you have at least 5 years of experience as a software engineer?", "No"),
    ("Do you have 4+ years of B2B SaaS sales experience?", "No"),
    ("Do you have Elastic Observability or Security experience?", "No"),
    ("Do you have 10+ years of network engineering experience?", "No"),
    ("Have you owned lifecycle marketing programs in-house at a SaaS company?", "No"),
])
def test_experience_is_claimed_only_where_the_resume_shows_it(question, expected):
    assert said(question, YN) == expected


def test_a_stance_of_ask_leaves_unproven_experience_for_the_owner():
    owner = config.UserProfile(**{**OWNER.__dict__, "unproven_experience_answer": "ask"})
    assert said("Do you have 4+ years of B2B SaaS sales experience?", YN, profile=owner) == ""


@pytest.mark.parametrize("question", [
    "Do you have a Masters degree and 10+ years of industry experience or a Ph.D and 6+ years of industry experience?",
    "Do you hold an advanced degree (masters or Ph.D) in one of the following fields?: Mathematics, Statistics",
])
def test_a_degree_question_with_more_in_it_than_the_level_is_left_alone(question):
    assert said(question, YN) == ""


@pytest.mark.parametrize("question, expected", [
    ("Are you pursuing or have you completed a PhD?", "No"),
    ("Do you have a Master's degree?", "Yes"),
    ("Have you earned a bachelor's degree?", "Yes"),
])
def test_degree_level_follows_the_owners_education(question, expected):
    assert said(question, YN) == expected


@pytest.mark.parametrize("question, expected", [
    ("Are you a current Globex employee?", "No"),
    ("Have you previously been employed by Globex in any capacity?", "No"),
    ("Do you currently or have you previously worked for Acme Corp?", "Yes"),
    ("To the best of your knowledge, do you have any family members or personal relationships at Globex or its vendors?", "No"),
])
def test_ties_to_the_company_come_from_the_work_history_and_the_profile(question, expected):
    assert said(question, YN) == expected


def test_a_free_text_box_is_never_answered_yes_or_no():
    assert said("If you currently work, or have previously worked, at Acme Corp, please provide your Employee ID (EID).") == ""


def test_government_relatives_are_a_different_fact_and_left_alone():
    assert said("Are you a close relative of a government official?", YN) == ""


def test_how_did_you_hear_is_answered_from_the_owners_source_and_the_list_on_offer():
    assert said("How did you hear about this role?") == "LinkedIn"
    assert said("How did you first learn about Globex as an employer?", ["Globex blog", "LinkedIn", "Other"]) == "LinkedIn"
    assert said("How did you hear about us?", ["Job board", "Company careers site", "Other"]) in ("Job board", "Company careers site")
    assert said("Where can we learn more about you?") == ""


def test_employer_name_location_and_names():
    assert said("What is the name of your current or most recent employer?") == "Acme Corp"
    assert said("Who is your current or previous employer?") == "Acme Corp"
    assert said("Current Location") == "Fairfax, Virginia, United States"
    assert said("Current Location", ["Canada - Ontario", "US - New York", "US - West Virginia", "US - Virginia"]) == "US - Virginia"
    assert said("What is your legal first name?") == "Pat"
    assert said("What's the name you'd prefer us to use?") == "Pat"
    assert said("Name Pronunciation") == ""


def test_available_locations_pick_the_offices_the_owner_named():
    offices = ["Atlanta", "Boston", "New York City", "Remote", "Tokyo"]
    assert said("In what location(s) are you available to work?", offices, role="checkbox") == "Atlanta; New York City; Remote"


def test_preferences_and_alerts():
    assert said("How should we communicate with you?", ["Email", "Phone Call", "Text Message"]) == "Email"
    assert said("If this role offers the option to work remotely, do you plan to work remotely?", ["Yes, I intend to work remotely.", "No, I intend to work from an office."]).startswith("Yes")
    assert said("Are you willing to work from the office(s) listed on the job description?", YN) == "Yes"
    assert said("Receive alerts for similar jobs?", YN) == "No"
    assert said("Do you hold an active TS/SCI clearance with a Full Scope Poly?", YN) == "No"


def test_most_proficient_programming_language_comes_from_the_resume():
    assert said("What is your most proficient programming language?") == "Python"


def test_with_no_evidence_the_engine_says_nothing():
    bare = config.UserProfile()
    assert ae.answer(ask("Are you fluent in German?", YN, "combobox", profile=bare)) == ("", "")
    assert ae.answer(ask("What is your current employer?", profile=bare)) == ("", "")
    assert said("Do you have 3+ years of BGP experience?", YN, resume="") == ""


def test_location_inside_a_job_or_degree_entry_is_that_entrys_not_the_owners():
    entry = ae.Ask(question="Location", role="textbox", profile=OWNER, container="Work Experience")
    assert ae.answer(entry) == ("", "")
    assert said("Location") == ""                                          # a bare Location may be a job's: never guessed
    assert said("Current Location") == "Fairfax, Virginia, United States"


def test_who_the_owner_is_is_never_the_engines_to_answer():
    assert said("Have you ever served in the Armed Forces of the United States, National Oceanographic and Atmospheric Administration?", YN) == ""
    assert said("Are you a citizen of India?", YN) == "Yes"                  # citizenship is a fact the profile states


def test_optional_unknowns_are_left_blank_only_on_a_page_that_marks_what_is_required():
    import page_agent
    asked = ["Why do you want to work here?*", "Website", "Twitter", "Cumulative GPA*"]
    todo, blank = page_agent.split_optional(asked, {"Why do you want to work here?", "Cumulative GPA"})
    assert todo == ["Why do you want to work here?*", "Cumulative GPA*"] and blank == ["Website", "Twitter"]
    # a form that marks nothing required: every box stays open (a reader may still have to answer them)
    todo, blank = page_agent.split_optional(asked, set())
    assert todo == asked and blank == []


def test_knowledge_and_range_questions_follow_the_resume():
    assert said("Do you have working knowledge of government buying cycles and procurement processes?", YN) == "No"
    assert said("Have you directly supported Product teams in a finance business partner capacity?", YN) == "No"
    assert said("Do you have working knowledge of OSPF routing?", YN) == "Yes"
    bins = ["Less than 8 years", "8+ years"]
    assert said("How many years of industry experience in a full-time Software Engineer role do you have?", bins) == "Less than 8 years"
    assert said("How many years of network engineering experience do you have?", ["0-2 years", "3-5 years", "6+ years"]) == "6+ years"


def test_a_privacy_acknowledgement_is_accepted_but_never_a_declaration_or_a_data_sharing_agreement():
    privacy = ["I acknowledge that I have read and understood the Candidate Privacy Notice.", "I do not acknowledge"]
    assert said("Please review the linked document:", privacy).startswith("I acknowledge that I have read")
    assert said("Candidate AI Usage Attestation:", ["I agree", "I do not agree"]) == ""
    assert said("Application declaration", ["I certify that the information is true and correct", "No"]) == ""
    assert said("Sharing", ["I agree to share my data with partners", "No"]) == ""
    declining = config.UserProfile(**{**OWNER.__dict__, "accept_application_privacy_prompts": False})
    assert said("Please review the linked document:", privacy, profile=declining) == ""
