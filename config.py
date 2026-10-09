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
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import diagnostics
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
PROFILE_PATH = Path(os.getenv("PROFILE_PATH", str(DATA_DIR / "profile.json")))
# The owner's standard resume, used only when RESUME_SOURCE=master.
MASTER_RESUME_PATH = BASE_DIR / "assets" / "master_resume.pdf"


@dataclass(frozen=True)
class UserProfile:
    """The client's job-search profile.

    These are placeholder defaults only -- never real identifying data. The
    actual profile lives in the gitignored data/profile.json and is merged
    in by get_user_profile() below. See data/profile.example.json for the
    template.
    """

    full_name: str = "Your Name"
    first_name: str = "Your"
    middle_name: str = ""
    last_name: str = "Name"
    email: str = "you@example.com"
    phone: str = ""
    phone_mobile: str = ""
    phone_home: str = ""
    phone_work: str = ""
    target_titles: tuple[str, ...] = ()
    prefix: str = ""
    address_line1: str = ""
    address_line2: str = ""       # apartment, suite, unit -- empty when there is none
    city: str = ""
    state: str = ""
    county: str = ""  # some ATS forms require county separately
    postal_code: str = ""
    current_location: str = ""  # used to autofill combined "current location" form fields
    locations: tuple[str, ...] = ()  # preferred, not exclusive
    open_to_relocation: bool = False
    salary_min: int = 0
    salary_max: int = 0
    years_experience: int = 0
    requires_visa_sponsorship: bool = False
    linkedin_url: str = ""
    portfolio_url: str = ""

    # Standard application answers. Leaving these blank means the agent
    # leaves the question for you rather than guessing.
    work_authorization: str = ""
    us_citizen: str = ""
    country_of_citizenship: str = ""
    security_clearance_level: str = ""
    legally_eligible_to_work: str = ""
    availability_to_start: str = ""
    willing_to_travel: str = ""
    security_clearance: str = ""
    felony_conviction: str = ""
    previously_employed_here: str = ""
    applied_here_before: str = ""
    willing_to_work_weekends: str = "Yes"
    relatives_employed_here: str = ""
    willing_drug_test_and_physical: str = ""
    veteran_status: str = ""
    disability_status: str = ""
    ethnicity: str = ""
    gender: str = ""
    work_arrangements: tuple[str, ...] = ()
    work_type: str = ""
    # The most recent employer, as questionnaires ask for it. These should
    # match the resume word for word, so the form and the document agree.
    current_employer: str = ""
    current_position_title: str = ""
    current_employer_type: str = ""
    current_employment_dates: str = ""
    reason_for_leaving: str = ""
    # Why each job ended, in your own words. A work-history entry takes the
    # reason for its own employer, not the current one's.
    reasons_for_leaving: tuple[tuple[str, str], ...] = ()
    current_employer_location: str = ""
    employment_statuses: tuple[str, ...] = ()  # Text field or checkboxes: select all that apply
    how_did_you_hear: str = ""
    preferred_contact_method: str = ""
    bound_by_non_compete: str = ""
    # Answers the agent may give only because you stated them. Leave a value
    # empty and the agent leaves that question for you rather than guessing.
    hispanic_or_latino: str = ""
    at_least_18: str = ""
    authorized_for_any_employer: str = ""
    willing_to_submit_to_pre_employment_background_check: str = ""
    worked_for_occ: str = ""
    provided_services_to_occ: str = ""
    bonus_expectations: str = ""
    willing_to_work_onsite_three_days: str = ""
    preferred_language: str = ""
    people_managed: str = ""
    outside_business_interests_with_competitors: str = ""
    # Asked by many forms, and only yours to answer (30 September: with none of these in the profile the AI
    # guessed them). Empty = the question comes to you once, and your answer is kept.
    preferred_name: str = ""           # empty = you have none: the box is left blank
    gender_identity: str = ""          # empty = your `gender` answers "gender identity" too
    sexual_orientation: str = ""
    transgender: str = ""
    pronouns: str = ""
    gpa: str = ""
    certifications: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    # "Skill: level" on a 1-5 scale, for "rate your skill with ..." questions: ("Cisco: 5", "Palo Alto: 4").
    skill_levels: tuple[str, ...] = ()
    # Degrees from the resume, newest first: (degree level, field, school, year).
    education: tuple[tuple[str, str, str, str], ...] = ()
    # The same degrees with the dates forms ask for: (school, started, finished).
    education_dates: tuple[tuple[str, str, str], ...] = ()
    # What the break between jobs was, for forms that ask about gaps.
    work_history_gap: str = ""
    # Substitutes the OWNER has approved for a value a list may not offer, tried in order after the
    # exact value fails and never otherwise: {"Computer Technology": ["Computer Science"]}. A field of
    # study is a fact the application certifies, so the agent never swaps it on its own.
    answer_alternatives: dict = field(default_factory=dict)
    country: str = ""
    phone_country_code: str = ""
    # Application-form pop-ups like "Data Privacy Agreement" that block the
    # form until accepted. On = the agent accepts them itself. Cookie banners
    # are handled separately and are not affected.
    accept_application_privacy_prompts: bool = False
    # Signature and certification checkboxes ("equivalent to a handwritten
    # signature", "I certify the information is true and complete"): True =
    # the agent signs them on your behalf, but only once every other answer
    # on that page came from this profile, nothing contradicts it and
    # nothing required is left blank. False = the agent stops at each one
    # and carries on by itself once you have signed.
    sign_attestations: bool = False
    # The agent's browser is signed in to the application email's Google
    # account. When the site shows no confirmation, the agent may look in
    # that Gmail (read-only search results) for the employer's "application
    # received" email.
    check_gmail_for_confirmation: bool = False


# The first default, gemini-2.5-flash, answered "no longer available to new users" on the owner's key (25
# September 2026): the models a key can use change, so GEMINI_MODEL in .env overrides this.
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Google's free tier gives every model its own allowance (AI Studio's Rate Limit page, 29 September 2026): each
# Flash about 20 requests a day, each Flash Lite about 500, Gemma thousands but only ~16,000 tokens a minute.
# One model alone ran out after two or three applications. So each kind of call has a ladder of models, tried
# in order; one that is spent rests until Google's daily reset and the next answers (gemini_integration.py).
# "page": planning a whole page and writing open answers -- the stronger models first.
# "quick": one dropdown choice, one short question, a screenshot -- the models with big allowances first.
# GEMINI_PAGE_MODELS / GEMINI_QUICK_MODELS in .env (comma-separated) replace these lists.
DEFAULT_GEMINI_PAGE_MODELS = ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
                              "gemini-3-flash-preview", "gemini-2.5-flash", "gemini-3.5-flash-lite",
                              "gemini-3.1-flash-lite", "gemma-4-31b-it")
DEFAULT_GEMINI_QUICK_MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite",
                               "gemma-4-31b-it", "gemma-4-26b-a4b-it", "gemini-3.8-flash", "gemini-3.7-flash",
                               "gemini-3.6-flash", "gemini-3.5-flash")
# Where the free allowances start again: midnight in Google's Pacific time.
GEMINI_QUOTA_RESET_TZ = "America/Los_Angeles"
GEMINI_QUOTA_RESET_UTC_OFFSET_HOURS = -8     # used if this computer has no time-zone database


def _models_from_env(name: str, default: tuple) -> tuple:
    listed = tuple(m.strip() for m in os.getenv(name, "").split(",") if m.strip())
    return listed or default


@dataclass(frozen=True)
class AppConfig:
    diagnostic_retention_days: int = field(default_factory=diagnostics.retention_days)
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    )
    # Google's Gemini, used only when FORM_ANSWER_MODE=gemini (gemini_integration.py): it answers the
    # questions on the application pages, and Claude still writes the resume and the cover letter.
    # The key comes from aistudio.google.com; it is never logged and never put in an address.
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", "").strip())
    gemini_model: str = field(
        default_factory=lambda: os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL)
    gemini_base_url: str = field(
        default_factory=lambda: os.getenv("GEMINI_BASE_URL", "").strip() or DEFAULT_GEMINI_BASE_URL)
    gemini_page_models: tuple = field(
        default_factory=lambda: _models_from_env("GEMINI_PAGE_MODELS", DEFAULT_GEMINI_PAGE_MODELS))
    gemini_quick_models: tuple = field(
        default_factory=lambda: _models_from_env("GEMINI_QUICK_MODELS", DEFAULT_GEMINI_QUICK_MODELS))
    # The same two ladders when Claude or OpenAI answers the forms (FORM_ANSWER_MODE=claude / openai), chosen
    # on Settings. Empty means the provider's one model (ANTHROPIC_MODEL / OPENAI_MODEL).
    claude_page_models: tuple = field(default_factory=lambda: _models_from_env("CLAUDE_PAGE_MODELS", ()))
    claude_quick_models: tuple = field(default_factory=lambda: _models_from_env("CLAUDE_QUICK_MODELS", ()))
    openai_page_models: tuple = field(default_factory=lambda: _models_from_env("OPENAI_PAGE_MODELS", ()))
    openai_quick_models: tuple = field(default_factory=lambda: _models_from_env("OPENAI_QUICK_MODELS", ()))
    openai_base_url: str = field(
        default_factory=lambda: os.getenv("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1")
    # Who writes the resume and the cover letter, chosen on Settings, as "provider:model" (e.g.
    # "gemini:gemini-3.5-flash"), and who writes them if that fails. Unset: Claude, then Gemini, then OpenAI.
    resume_writer: str = field(default_factory=lambda: os.getenv("RESUME_WRITER", "").strip())
    resume_writer_fallback: str = field(default_factory=lambda: os.getenv("RESUME_WRITER_FALLBACK", "").strip())
    # OpenAI API key for resume tailoring fallback (optional).
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", "").strip())
    openai_model: str = field(
        default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o").strip())
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
    # Deprecated compatibility flag. Automatic submission is authorized only
    # by auto_submit_verified_only; keeping this field avoids breaking callers
    # that still read AUTO_SUBMIT from older configuration files.
    auto_submit: bool = field(
        default_factory=lambda: os.getenv("AUTO_SUBMIT", "").strip().lower() in {"1", "true", "yes", "on"}
    )
    # "profile" is the autonomous mode: every form answer comes from the
    # saved profile or local answer library, not an LLM or session planner.
    # "claude" preserves the previous assisted page-planning workflow (who plans
    # each page is then AGENT_BRAIN: the API or the Claude Code session).
    # "gemini" has Google's Gemini answer the pages and Claude write the documents;
    # AGENT_BRAIN is not consulted.
    form_answer_mode: str = field(
        default_factory=lambda: os.getenv("FORM_ANSWER_MODE", "profile").strip().lower()
    )
    # Claude always tailors the resume/CV for a job. Cover letters are a
    # separate, explicit opt-in so document-only mode does not make other API
    # calls while filling an application.
    generate_cover_letters: bool = field(
        default_factory=lambda: os.getenv("GENERATE_COVER_LETTERS", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    # Which agent works the application pages. "reader" (the default) reads each
    # page the way a screen reader does and has Claude plan it (page_agent.py);
    # "rules" is the older engine built from per-site rules, kept as a fallback.
    agent_engine: str = field(default_factory=lambda: os.getenv("AGENT_ENGINE", "reader").strip().lower())
    # Who works out the answers for each page. "session" hands the page to the
    # Claude Code session the owner is talking to (session_planner.py) and costs
    # no API credit; "api" calls Anthropic directly for every page.
    agent_brain: str = field(default_factory=lambda: os.getenv("AGENT_BRAIN", "api").strip().lower())
    # How many times a flaky page action is retried before it is reported.
    action_retries: int = 3
    # Which resume an application gets: "tailored" (the default) is the one
    # Claude tailored to this job; "master" is assets/master_resume.pdf.
    # See resume_to_attach().
    resume_source: str = field(
        default_factory=lambda: os.getenv("RESUME_SOURCE", "tailored").strip().lower())
    # What the agent does with a value the site put on the form (a resume
    # parser's guess, a list's first entry) that contradicts the profile:
    # "correct" (the default) puts it right from the profile and lists the
    # correction at hand-over; "leave" leaves it for the owner. A value the
    # owner entered is never changed either way. See site_prefill_policy().
    site_prefill_policy: str = field(default_factory=lambda: site_prefill_policy())


# Profile fields where an empty value is itself the answer -- there is none -- rather than "not stated yet".
# A form's box for one of these is left blank; any other empty field is a question for the owner.
BLANK_MEANS_NONE = frozenset({"middle_name", "preferred_name", "prefix", "security_clearance_level",
                              "phone_home", "phone_work", "portfolio_url", "address_line2"})


def get_user_profile() -> UserProfile:
    defaults = UserProfile()
    if not PROFILE_PATH.is_file():
        return defaults
    try:
        raw = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("profile must be a JSON object")
        allowed = set(defaults.__dataclass_fields__)
        values = {key: value for key, value in raw.items() if key in allowed}
        return UserProfile(**{**defaults.__dict__, **values})
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not load profile from {PROFILE_PATH}: {exc}") from exc


def site_prefill_policy() -> str:
    """"correct" or "leave": what happens to a site's value that contradicts
    the profile (SITE_PREFILL_POLICY in .env; "correct" unless set)."""
    value = os.getenv("SITE_PREFILL_POLICY", "correct").strip().lower()
    return value if value in ("correct", "leave") else "correct"


def resume_to_attach(tailored, app_config=None) -> Optional[Path]:
    """The one resume an application gets.

    By default the resume tailored to this job. With RESUME_SOURCE=master,
    the owner's standard resume at assets/master_resume.pdf, when it exists.
    Every place that attaches or verifies a resume asks here, so the
    uploader and the submit gate can never disagree about which file is
    the right one -- they did when three places each forced the master
    file on their own while the gate still expected the tailored one.
    """
    source = str(getattr(app_config, "resume_source", "")
                 or os.getenv("RESUME_SOURCE", "tailored")).strip().lower()
    if source == "master" and MASTER_RESUME_PATH.is_file():
        return MASTER_RESUME_PATH
    return Path(tailored) if tailored else None


def get_app_config() -> AppConfig:
    """Returns the app config. No API key is required -- if none are set the
    agent skips tailoring and attaches the local resume."""
    return AppConfig()


def available_tailor_providers(cfg: AppConfig) -> list[str]:
    """Returns the list of AI providers that have an API key configured,
    in priority order: Claude -> Gemini -> OpenAI.
    An empty list means no AI tailoring is possible; the local resume is used."""
    providers = []
    if cfg.anthropic_api_key:
        providers.append("claude")
    if cfg.gemini_api_key:
        providers.append("gemini")
    if cfg.openai_api_key:
        providers.append("openai")
    return providers
