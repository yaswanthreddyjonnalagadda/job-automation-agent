"""
Configuration for the job application assistant.

Loads secrets (Anthropic API key, ATS account password) from environment
variables via .env. ATS_PASSWORD is for employer application-tracking-system
accounts (Workday, Greenhouse, Lever, iCIMS, etc.) that you create yourself
per-employer -- NOT for linkedin.com, indeed.com, or dice.com. Those three
are hard-blocked from scripted login in browser_automation.py regardless of
what's in this file; see BLOCKED_LOGIN_DOMAINS there.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
LOG_DIR = BASE_DIR / "logs"
BROWSER_PROFILE_DIR = BASE_DIR / "browser_profile"

for d in (DATA_DIR, OUTPUT_DIR, LOG_DIR, BROWSER_PROFILE_DIR):
    d.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "applications.db"
RESUME_PATH = os.getenv("RESUME_PATH", str(DATA_DIR / "resume.pdf"))
JOB_QUEUE_PATH = os.getenv("JOB_QUEUE_PATH", str(DATA_DIR / "job_queue.txt"))


@dataclass(frozen=True)
class UserProfile:
    """The client's job-search profile. Not secret -- safe to keep in code."""

    full_name: str = "Yaswanth Reddy Jonnalagadda"
    # Split as the owner gives it (2026-09-17), not guessed from the full name:
    # forms were filled with "Yaswanth" / "Reddy Jonnalagadda".
    first_name: str = "Yaswanth Reddy"
    middle_name: str = ""          # none
    last_name: str = "Jonnalagadda"
    email: str = "jonnalagaddayaswanth06@gmail.com"
    phone: str = "(571) 354-5212"  # from your resume -- correct this if it's wrong/outdated
    phone_mobile: str = "(571) 354-5212"  # mobile/cell phone
    phone_home: str = ""  # home phone (optional)
    phone_work: str = ""  # work phone (optional)
    target_titles: tuple[str, ...] = (
        "Network Engineer",
        "Cloud Network Engineer",
        "Senior Network Engineer",
    )
    prefix: str = "Mr."
    address_line1: str = "9365 Lee Hwy"
    city: str = "Fairfax"
    state: str = "Virginia"
    county: str = "Fairfax"  # some ATS forms require county separately
    postal_code: str = "22031"
    current_location: str = "Fairfax, VA"  # used to autofill combined "current location" form fields
    locations: tuple[str, ...] = ("Remote", "Fairfax, VA", "Arlington, VA")  # preferred, not exclusive
    open_to_relocation: bool = True  # anywhere in the US -- remote, hybrid, or fully onsite all fine
    salary_min: int = 125_000
    salary_max: int = 185_000
    years_experience: int = 6
    requires_visa_sponsorship: bool = True  # on H-1B; a transfer counts as sponsorship
    linkedin_url: str = "https://www.linkedin.com/in/yaswanthreddyjonnalagadda"
    portfolio_url: str = ""

    # Standard application answers, supplied by the user on 2026-09-14 so
    # these questions don't have to be asked per-employer. The EEO ones
    # (veteran/disability/ethnicity/gender) are voluntary self-identification
    # the user chose to disclose -- never infer or change them.
    work_authorization: str = "H-1B"
    # Supplied by the user on 2026-09-15 so these are never asked again.
    us_citizen: str = "No"
    country_of_citizenship: str = "India"  # confirmed by the user, not inferred
    security_clearance_level: str = "None"
    legally_eligible_to_work: str = "Yes"
    availability_to_start: str = "Immediately"
    willing_to_travel: str = "Greater than 50%"  # user is open to up to 100%
    security_clearance: str = "None"
    felony_conviction: str = "No"
    previously_employed_here: str = "No"  # default; verify if the user has history with an employer
    # The owner's answers (2026-09-18), asked once so no form has to ask again.
    applied_here_before: str = "No"
    relatives_employed_here: str = "No"
    willing_drug_test_and_physical: str = "Yes"
    veteran_status: str = "I am not a veteran"
    disability_status: str = "No, I do not have a disability"
    ethnicity: str = "Asian"
    gender: str = "Male"
    work_arrangements: tuple[str, ...] = ("In Person Office", "Remote Virtual", "Hybrid")
    work_type: str = "Full-Time"
    # The most recent employer, as questionnaires ask for it. These match the
    # resume word for word, so the form and the document say the same thing.
    current_employer: str = "Capital One"
    current_position_title: str = "Senior Network and Security Engineer"
    current_employer_type: str = "Financial Services"
    current_employment_dates: str = "February 2025 - Present"
    reason_for_leaving: str = "Currently employed"
    current_employer_location: str = "McLean, VA"
    employment_statuses: tuple[str, ...] = ("Full-Time",)  # Text field or checkboxes: select all that apply
    # Where the user actually finds these postings (their answer, 2026-09-16).
    how_did_you_hear: str = "LinkedIn"
    # How an employer should reach you first. Dayforce asks it outright.
    preferred_contact_method: str = "Email"
    # Supplied by the user on 2026-09-15 (IGT application).
    bound_by_non_compete: str = "No"
    # Answers the agent may give only because the user stated them. Leave a
    # value empty and the agent leaves that question for you rather than
    # guessing at it.
    hispanic_or_latino: str = "No"          # from the user's 2026-09-14 EEO answers
    at_least_18: str = "Yes"
    authorized_for_any_employer: str = "No"  # on an H-1B: authorized, but not for any employer
    preferred_language: str = "English"
    people_managed: str = ""                 # unknown -- the user fills this in
    outside_business_interests_with_competitors: str = "No"
    # Degrees from the resume, newest first: (degree level, field, school, year).
    education: tuple[tuple[str, str, str, str], ...] = (
        ("Master's", "Computer Technology", "Eastern Illinois University", "2022"),
        ("Bachelor's", "Electrical and Electronics Engineering", "JNTU Hyderabad", "2019"),
    )
    country: str = "United States"
    phone_country_code: str = "+1"
    # Application-form pop-ups like "Data Privacy Agreement" that block the
    # form until accepted. On = the agent accepts them itself (2026-09-15).
    # Cookie banners are handled separately and are not affected.
    accept_application_privacy_prompts: bool = True
    # Signature and certification checkboxes ("equivalent to a handwritten
    # signature", "I certify the information is true and complete"): the owner's
    # decision (2026-09-17) is that the agent signs them on their behalf -- but
    # only once every other answer on that page came from this profile, nothing
    # contradicts it and nothing required is left blank. False = the agent stops
    # at each one and carries on by itself once you have signed.
    sign_attestations: bool = True
    # The agent's browser is signed in to the application email's Google
    # account. When the site shows no confirmation, the agent may look in
    # that Gmail (read-only search results) for the employer's "application
    # received" email. The user's decision, 2026-09-15.
    check_gmail_for_confirmation: bool = True


@dataclass(frozen=True)
class AppConfig:
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    )
    resume_path: Path = Path(RESUME_PATH)
    job_queue_path: Path = Path(JOB_QUEUE_PATH)
    # Used only for employer ATS accounts (Workday/Greenhouse/Lever/iCIMS...)
    # that you create yourself per-employer. Never used for linkedin.com,
    # indeed.com, or dice.com -- those are hard-blocked in browser_automation.py.
    ats_email: str = field(default_factory=lambda: os.getenv("ATS_EMAIL", ""))
    ats_password: str = field(default_factory=lambda: os.getenv("ATS_PASSWORD", ""))
    db_path: Path = DB_PATH
    output_dir: Path = OUTPUT_DIR
    log_dir: Path = LOG_DIR
    browser_profile_dir: Path = field(
        default_factory=lambda: Path(os.getenv("BROWSER_PROFILE_DIR", "") or BROWSER_PROFILE_DIR))
    # Which browser to drive. "chrome" is real Google Chrome as installed --
    # same build, same rendering, same fonts as the one in the taskbar -- so an
    # employer's form behaves as it does for the user. "chromium" falls back to
    # the build Playwright ships. Empty means: Chrome if it is installed.
    browser_channel: str = field(default_factory=lambda: os.getenv("BROWSER_CHANNEL", "chrome").strip())
    # Headless=False on purpose: the human must see the browser to log in
    # and to review/submit each application themselves.
    browser_headless: bool = False
    claude_max_retries: int = 3
    claude_request_timeout: float = 60.0
    # Opt-in verified auto-submit. OFF unless AUTO_SUBMIT_VERIFIED_ONLY=true is
    # set in .env. Even when on, the agent submits only if every required field
    # exactly matches approved profile/resume data, the job identity and the
    # uploaded documents match the tracked application, and nothing uncertain
    # (CAPTCHA, attestation, ambiguous choice, warning) is on the page --
    # see safety.evaluate_auto_submit().
    auto_submit_verified_only: bool = field(
        default_factory=lambda: os.getenv("AUTO_SUBMIT_VERIFIED_ONLY", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    # The owner's decision (2026-09-17): submit an application automatically
    # once it is complete. AUTO_SUBMIT=true in .env; set it to false to go back
    # to clicking Submit yourself. See safety.ready_to_auto_submit() for what
    # "complete" means -- and what still always waits for a person.
    auto_submit: bool = field(
        default_factory=lambda: os.getenv("AUTO_SUBMIT", "").strip().lower() in {"1", "true", "yes", "on"}
    )
    # Which agent works the application pages. "reader" (the default) reads each
    # page the way a screen reader does and has Claude plan it (page_agent.py);
    # "rules" is the older engine built from per-site rules, kept as a fallback.
    agent_engine: str = field(default_factory=lambda: os.getenv("AGENT_ENGINE", "reader").strip().lower())
    # How many times a flaky page action is retried before it is reported.
    action_retries: int = 3


def get_user_profile() -> UserProfile:
    return UserProfile()


def get_app_config() -> AppConfig:
    cfg = AppConfig()
    if not cfg.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in."
        )
    return cfg
