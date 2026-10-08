"""
Chat-mediated application flow: drives the browser through login and a
multi-step application wizard (Workday etc. commonly split it into
Create Account / My Information / My Experience / Application Questions /
Voluntary Disclosures / Review), stopping and writing a review package
(screenshot + JSON summary) at EVERY step instead of calling input() --
because this is meant to be launched from a tool session with no live
terminal attached.

A human (via chat, relayed by whoever launched this) reviews each step's
summary and writes one of these to the signal file:
  "continue"      -- advance to the next step (clicks Next/Continue -- this
                     is just page navigation within the draft, nothing final)
  "submit"        -- click the final submit button (only meaningful on the
                     last, Review step -- never called without this signal)
  "skip"          -- stop here, mark the application as skipped in the tracker
  "refresh"       -- re-detect/re-screenshot the current page without
                     clicking anything (e.g. after a manual edit)
  "fill_experience" -- run fill_experience_section/fill_education_section
                     using --experience-json
  "goto:<url>"    -- send the browser back to a page it has wandered off
                     (a sign-in redirect, or the user navigating), keeping
                     the run and its part-filled form
  "reload_code"   -- hot-reload browser_automation.py's code into this
                     already-running process (see reload_browser_automation
                     below) instead of restarting the whole script -- keeps
                     the SAME browser tab/session open across a code fix

Usage:
    python apply_flow.py <job_input.json> --signal-file <path> [--timeout SECONDS]
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
from datetime import datetime, timezone
from typing import Optional
import logging
import sys
from urllib.parse import urlparse
from pathlib import Path

import re

import browser_automation
import config as config_module
from resume_pdf import build_letter_pdf, build_resume_pdf
from browser_automation import BlockedLoginDomainError, JobApplicationAssistant
from claude_integration import ClaudeClient, ClaudeIntegrationError
from config import get_app_config, get_user_profile
from jd_analyzer import build_job_description, dedup_key_for_url, submission_effect_key_for_url
import ai_choice
import answer_bank
import safety
from safety import STATUS_DISQUALIFIED_POLICY_MISMATCH
from state_machine import (
    STATUS_BLOCKED_VALIDATION_LOOP,
    StateFingerprintCircuitBreaker,
    dump_forensic_failure,
)
from job_tracker import (
    STATUS_FORM_FILLED, STATUS_NEEDS_USER_REVIEW, STATUS_READY_TO_SUBMIT, STATUS_SKIPPED,
    STATUS_SUBMITTED, JobTracker,
)
from resume_parser import parse_resume

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("anthropic").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("apply_flow")

# Nothing the agent logs may carry an API key, a password, an email address or
# a phone number -- log files get shared far more casually than the database.
safety.install_log_redaction()
safety.install_log_redaction("browser_automation")


class JsonLogHandler(logging.Handler):
    """One JSON object per line, for reading a run back by machine."""

    def __init__(self, path: Path):
        super().__init__()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "event": (record.getMessage().split(":", 1)[0][:40]
                          if record.getMessage()[:40].isupper() else ""),
                "message": safety.redact(record.getMessage())[:2000],
            }
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except Exception:
            pass


def refresh_answer_bank(tracker) -> None:
    """Rebuilds the answer bank from the tracker (answer_bank.py): when a run starts, and after each wait for the owner,
    so what they answered or sent meanwhile is reused at once. Never stops a run."""
    if tracker is None:
        return
    try:
        held = answer_bank.rebuild(tracker)
        logger.info("ANSWER_BANK: %d question(s) answered before can be answered on any portal", held)
    except Exception as exc:
        logger.warning("ANSWER_BANK: not rebuilt (%s)", str(exc).splitlines()[0][:120] if str(exc) else type(exc).__name__)


def open_tracker(config):
    """The tracker, as tracking.open_tracker() decides: Postgres when configured, otherwise SQLite."""
    import tracking
    tracker = tracking.open_tracker(getattr(config, "db_path", None))
    logger.info("Tracking to %s", "SQLite" if isinstance(tracker, JobTracker) else "Postgres")
    return tracker


def phone_for_documents(profile) -> str:
    """The phone number as it appears on the resume and cover letter.

    With the country code in front -- "+1 (571) 354-5212" -- so a site that
    fills its form from the resume takes the country along with the number.
    The owner's decision (2026-09-17). Forms themselves still get the number
    as the profile holds it.
    """
    phone = (getattr(profile, "phone", "") or "").strip()
    code = (getattr(profile, "phone_country_code", "") or "").strip()
    if not phone or not code or phone.startswith("+"):
        return phone
    return f"{code} {phone}"


def normalise_contact_details(text: str, profile) -> str:
    """Forces the profile's email and phone into generated documents.

    The tailoring is built from the source resume PDF, so it reproduces
    whatever contact details that file contains -- which left a THIRD email
    address on the resume while the form was filled with the current one. The
    profile is the single source of truth for how to reach the candidate."""
    if profile.email:
        text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", profile.email, text)
    if profile.phone:
        # Only rewrite phone numbers in the header block: the body legitimately
        # contains figures like '99.5%' and '$280K' that must not be touched.
        head, sep, body = text.partition("\n\n")
        # Any country code already written in front is replaced along with
        # the number, so it is never doubled ("+1 +1 (571)...").
        head = re.sub(r"(?:\+\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}",
                      phone_for_documents(profile), head)
        text = head + sep + body
    return text


SECTION_WORDS = {
    "summary", "profile", "objective", "experience", "skills", "education",
    "certifications", "projects", "professional summary", "work experience",
    "technical skills",
}


PREAMBLE_MARKERS = (
    "note:", "here is", "here's", "i've", "i have not", "below,", "below you",
    "as requested", "i reworded", "i kept", "this version", "summary of changes",
)


def strip_model_preamble(text: str, profile) -> str:
    """Removes commentary the model sometimes writes before the resume.

    One run opened with 'Note: Your background is strong in enterprise... I have
    not added any of that' -- a message to the candidate, which would have been
    typeset into the PDF sent to the employer. Only drops the lead-in when it
    actually reads like commentary, so real resume content is never cut."""
    lines = text.splitlines()
    surname = profile.full_name.split()[-1].lower()

    for i, line in enumerate(lines[:25]):
        stripped = line.strip().lower().rstrip(":")
        if not stripped or stripped == "---":
            continue
        if stripped in SECTION_WORDS or surname in stripped:
            if i == 0:
                return text
            head = "\n".join(lines[:i]).lower()
            if any(marker in head for marker in PREAMBLE_MARKERS):
                logger.warning(
                    "Stripped %d lines of model commentary from the resume (began %r)",
                    i, lines[0].strip()[:60],
                )
                return "\n".join(lines[i:]).lstrip()
            return text
    return text


def ensure_resume_header(text: str, profile, job) -> str:
    """Guarantees the resume starts with name / headline / contact.

    build_resume_pdf renders the first three lines as the name, headline and
    contact block. The tailoring doesn't always emit a header -- one run began
    at 'SUMMARY', which produced a PDF with 'SUMMARY' as the candidate's name
    and no contact details anywhere. A resume nobody can identify or reply to
    is worse than an untailored one, so the header is constructed here rather
    than hoped for."""
    lines = text.lstrip().splitlines()
    first = (lines[0].strip() if lines else "").lower().rstrip(":")
    surname = profile.full_name.split()[-1].lower()

    if first and surname in first:
        return text  # already has a proper name line

    if first in SECTION_WORDS or not first:
        logger.warning("Tailored resume had no name/contact header -- adding one")
    else:
        logger.warning("Tailored resume began with %r rather than a name -- adding a header", lines[0][:40])

    location = f"{profile.city}, {profile.state}" if profile.city else ""
    contact = " | ".join(p for p in (location, phone_for_documents(profile), profile.email) if p)
    header = f"{profile.full_name.upper()}\n{job.title}\n{contact}\n"
    return header + "\n" + text.lstrip()


def _whole_pdf(content: bytes) -> bool:
    """A PDF that starts as one and ends as one, with a page in it."""
    return content.startswith(b"%PDF") and b"%%EOF" in content[-2048:] and b"/Page" in content


def reuse_stored_document(tracker, key: str, kind: str, job_dir: Path) -> Path | None:
    """Returns the document already generated for this posting, restored from
    the database into the job's output folder, or None if there isn't one.

    A posting gets ONE tailored resume (and at most one cover letter). Retries
    used to re-tailor every time: CBTS ended up with three different resumes
    in the database for a single application, each costing a Claude call."""
    if tracker is None or not hasattr(tracker, "latest_document"):
        return None
    try:
        doc = tracker.latest_document(key, kind)
    except Exception as exc:
        logger.warning("Could not look up the stored %s: %s", kind, exc)
        return None
    if not doc or not doc.get("content"):
        return None
    content = bytes(doc["content"])
    # A broken PDF (cut off, or not a PDF at all) is discarded so the run retailors. Its size says nothing:
    # a text-only tailored resume is about 5 KB, and the old "under 20 KB is corrupt" rule threw away
    # Aristocrat's good resume (5,339 bytes, 29 September), so every retry would have written a new one.
    if kind == "resume" and doc.get("filename", "").lower().endswith(".pdf") and not _whole_pdf(content):
        logger.warning("REUSE_SKIPPED: stored resume %s is not a whole PDF (%d bytes) -- will retailor",
                       doc["filename"], len(content))
        return None
    path = job_dir / doc["filename"]
    if not path.is_file() or path.read_bytes() != content:
        path.write_bytes(content)
    return path


# ── out-of-credits / quota-exhausted detection ────────────────────────────────

_OUT_OF_CREDITS_PHRASES = (
    "usage limits",
    "credit balance",
    "billing",
    "quota exceeded",
    "insufficient_quota",
    "rate_limit_exceeded",   # OpenAI
    "resource_exhausted",    # Google
)


def _is_out_of_credits(exc: Exception) -> bool:
    """True when the error is a hard account-level limit (not a transient 503)."""
    msg = str(exc).lower()
    return any(phrase in msg for phrase in _OUT_OF_CREDITS_PHRASES)


def _make_tailor_client(provider: str, cfg) -> ClaudeClient:
    """A client that writes documents with *provider* (its default model)."""
    return ai_choice.writer_client(provider, "", cfg)


def _tailor_with(client, resume, job, profile, job_dir: Path, resume_txt: Path, resume_pdf: Path,
                 label: str) -> Path | None:
    """Attempt to tailor the resume using *client*.

    Returns the PDF path on success.
    Returns None on transient / unknown failure so the caller tries the next provider.
    Raises _OutOfCreditsError when the account has hit a hard usage limit, so the
    caller can warn the user and still try the next provider rather than silently
    falling through."""
    try:
        logger.info("Tailoring resume for %s via %s...", job.company, label)
        raw = client.tailor_resume(resume, job, profile)
        tailored = ensure_resume_header(
            normalise_contact_details(strip_model_preamble(raw, profile), profile),
            profile, job,
        )
        unsupported = safety.unsupported_claims(resume.raw_text, tailored)
        if unsupported:
            logger.warning("TAILORING_RETRY (%s): claimed %s, which isn't in the resume",
                           label, ", ".join(unsupported[:6]))
            try:
                retry = client.tailor_resume(
                    resume, job, profile,
                    extra_instruction=(
                        "Your previous draft claimed these, which do NOT appear in the candidate's "
                        f"resume: {', '.join(unsupported[:10])}. Write it again using only what the "
                        "resume actually says. Do not name specific product models, versions, metrics "
                        "or certifications unless the resume names them."
                    ),
                )
                retried = ensure_resume_header(
                    normalise_contact_details(strip_model_preamble(retry, profile), profile), profile, job)
                still = safety.unsupported_claims(resume.raw_text, retried)
                if len(still) < len(unsupported):
                    tailored, unsupported = retried, still
            except Exception as retry_exc:
                logger.warning("TAILORING_RETRY failed (%s) -- keeping the first draft", retry_exc)
        resume_txt.write_text(tailored, encoding="utf-8")
        build_resume_pdf(resume_txt, resume_pdf)
        logger.info("Tailored resume written to %s via %s", resume_pdf.name, label)
        return resume_pdf
    except Exception as exc:
        if _is_out_of_credits(exc):
            logger.warning(
                "AI_OUT_OF_CREDITS (%s): %s account has reached its usage limit -- "
                "add credits or wait for the limit to reset. Trying the next provider.",
                label, label,
            )
        else:
            logger.warning("TAILORING_FAILED via %s (%s)", label, exc)
        return None


def document_name(profile, kind: str, company: str) -> str:
    """The file name an employer sees: the applicant's own name, the document, the company.

    'Jane_Doe_Resume_Acme'. The name comes from the profile -- it was the owner's, written into the code, so every
    applicant's resume would have gone out under his name."""
    name = " ".join(filter(None, (getattr(profile, "first_name", ""), getattr(profile, "last_name", ""))))         or getattr(profile, "full_name", "")
    person = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    safe_company = re.sub(r"[^A-Za-z0-9]+", "", company or "")[:24] or "Job"
    return "_".join(part for part in (person, kind, safe_company) if part)


def prepare_materials(claude, resume, job, profile, job_dir: Path, tracker=None, key: str = "") -> Path:
    """Returns the resume PDF to attach for THIS job.

    Tailoring priority is driven by which API keys the user has configured in
    Settings (or .env). The order is always: Claude → Gemini → OpenAI.
    If none are configured, the local resume is attached immediately — no waiting,
    no silent fallback.

    Falls back to the local generic resume only after every configured provider
    has been tried."""
    generic = Path(config_module.RESUME_PATH)
    safe_company = re.sub(r"[^A-Za-z0-9]+", "", job.company)[:24] or "Job"
    reused = reuse_stored_document(tracker, key, "resume", job_dir)
    if reused is not None and reused.name != generic.name and not reused.name.endswith(f"Resume_{safe_company}.pdf"):
        # Written when the posting was read as someone else's: Schwab's was
        # tailored to its careers page's menu ("Career Schwab"), not the job.
        logger.info("RETAILORING: %s was written from a misread posting; tailoring it again", reused.name)
        reused = None
    if reused is not None and reused.name == generic.name:
        # That was the untailored fallback from a failed run -- tailor now
        # rather than locking the posting to the generic resume.
        reused = None
    if reused is not None:
        logger.info("REUSED_RESUME: %s was already tailored for this posting -- not generating another", reused.name)
        return reused

    resume_txt = job_dir / "tailored_resume.txt"
    resume_pdf = job_dir / f"{document_name(profile, 'Resume', job.company)}.pdf"

    # The writers the owner chose on Settings (RESUME_WRITER, then RESUME_WRITER_FALLBACK); with no choice,
    # every provider that has a key, Claude first (ai_choice.writers).
    cfg = config_module.get_app_config()
    found = ai_choice.writers(cfg)

    if not found:
        logger.info(
            "TAILORING_SKIPPED: no AI API keys configured -- attaching the local resume. "
            "Add a Claude, Gemini, or OpenAI key in Settings to enable tailoring."
        )
        return generic

    for label, client in found:
        result = _tailor_with(client, resume, job, profile, job_dir, resume_txt, resume_pdf, label)
        if result is not None:
            # Filed now, not when the form is reached: a run that failed before the form (the browser, a
            # sign-in, a stop) lost its resume, and every retry paid for a new one (Writer, 28 September:
            # 14 tailoring attempts for one job).
            store_materials(tracker, key, result, None)
            return result

    # All configured writers failed
    logger.error(
        "TAILORING_FAILED: every writer (%s) failed -- attaching the local generic resume.",
        ", ".join(label for label, _ in found),
    )
    return generic


def prepare_cover_letter(claude, resume, job, profile, job_dir: Path, tracker=None, key: str = "") -> tuple[Path, Path] | None:
    """Writes the cover letter as text + PDF and returns (txt, pdf). Only
    called once the form has shown it has a cover-letter field, so no Claude
    call is spent on a letter nobody will receive. Both files share a stem so
    store_materials finds the text twin of the PDF."""
    reused = reuse_stored_document(tracker, key, "cover_letter", job_dir)
    if reused is not None:
        try:
            if reused.suffix.lower() == ".pdf":
                twin = reused.with_suffix(".txt")
                if not twin.is_file():
                    doc = tracker.latest_document(key, "cover_letter") or {}
                    twin.write_text(doc.get("content_text") or "", encoding="utf-8")
                pair = (twin, reused)
            else:  # older runs stored the letter as plain text only
                pair = (reused, build_letter_pdf(reused, reused.with_suffix(".pdf")))
            logger.info("REUSED_COVER_LETTER: %s was already written for this posting", pair[1].name)
            return pair
        except Exception as exc:
            logger.warning("Stored cover letter unusable (%s) -- writing a new one", exc)

    letter_txt = job_dir / f"{document_name(profile, 'Cover_Letter', job.company)}.txt"
    letter_pdf = letter_txt.with_suffix(".pdf")
    try:
        logger.info("Form has a cover letter field -- writing one for %s...", job.company)
        letter = normalise_contact_details(claude.generate_cover_letter(resume, job, profile), profile)
        unsupported = safety.unsupported_claims(resume.raw_text + " " + job.raw_text, letter)
        # A letter naming something the resume doesn't used to be thrown away,
        # so RZR Global's form went without one over a single "DSP". It is
        # rewritten without those claims instead -- the resume is treated the
        # same way -- and only dropped if the rewrites still overreach.
        for attempt in range(2):
            if not unsupported:
                break
            logger.warning("COVER_LETTER_RETRY: it claimed %s, which isn't in the resume -- rewriting",
                           ", ".join(unsupported[:6]))
            letter = normalise_contact_details(claude.generate_cover_letter(
                resume, job, profile,
                extra_instruction=(
                    "Your previous draft mentioned these, which do NOT appear in the candidate's "
                    f"resume: {', '.join(unsupported[:10])}. Write it again without them, using only "
                    "what the resume actually says. Do not name technologies, products, figures or "
                    "certifications the resume does not name."
                ),
            ), profile)
            unsupported = safety.unsupported_claims(resume.raw_text + " " + job.raw_text, letter)
        if unsupported:
            logger.error("COVER_LETTER_REJECTED: still claimed %s after rewriting -- no letter attached",
                         ", ".join(unsupported[:6]))
            return None
        letter_txt.write_text(letter, encoding="utf-8")
        build_letter_pdf(letter_txt, letter_pdf)
        return letter_txt, letter_pdf
    except Exception as exc:
        logger.error("COVER_LETTER_FAILED (%s) -- leaving the cover letter field empty", exc)
        return None


def store_materials(tracker, key: str, resume_pdf: Path | None, cover_letter: Path | None) -> None:
    """Files the generated documents into the database, if the backend keeps
    documents. Never fatal -- a storage failure must not abort an application."""
    if not hasattr(tracker, "store_document"):
        return
    for kind, path in (("resume", resume_pdf), ("cover_letter", cover_letter)):
        if not path:
            continue
        try:
            text_twin = Path(path).with_suffix(".txt")
            text = text_twin.read_text(encoding="utf-8", errors="replace") if text_twin.is_file() else None
            tracker.store_document(key, kind, path, content_text=text)
        except Exception as exc:
            logger.warning("Could not store %s in the database: %s", kind, exc)


def decide_next_step(assistant, page, step: int, experience_data: dict, filled_experience: bool) -> str:
    """Picks the next action in --auto mode, replacing the human-sent signal.

    Deliberately conservative about one thing: it never returns 'submit'.
    Advancing a wizard is reversible; submitting is not, and sending an
    application to a real employer stays the user's decision."""
    # A CAPTCHA is the user's alone: stop and hand over, rather than pressing
    # the button that raised it again.
    if safety.captcha_visible(page):
        logger.info("A CAPTCHA is showing -- stopping for you to complete it")
        return "stop"
    if assistant.is_review_step(page):
        return "stop"

    # Work experience and education are only offered on the My Experience
    # step, and only need filling once.
    if not filled_experience and experience_data and assistant.has_experience_section(page):
        return "fill_experience"

    # Pre-navigation sweep: commit unfinalized experience/education cards before advancing
    try:
        from interaction import commit_draft_cards
        commit_draft_cards(page)
    except Exception:
        pass

    if assistant.has_next_step(page):
        return "continue"

    return "stop"


def approved_answers_file() -> dict:
    """Answers the user has explicitly approved for any application, from
    data/_approved_answers.json ({"label": "answer"}). Nothing else counts as
    an approved answer."""
    path = Path("data/_approved_answers.json")
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        logger.warning("Could not read approved answers: %s", exc)
    return {}


def collect_evidence(assistant, page, job_dir: Path, step: int) -> dict:
    """Full-page screenshot and page HTML, saved before any decision is made."""
    evidence_dir = job_dir / f"evidence_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_step{step}"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    try:
        shot = evidence_dir / "page.png"
        page.screenshot(path=str(shot), full_page=True)
        paths["screenshot"] = str(shot)
    except Exception as exc:
        logger.warning("Could not capture the screenshot: %s", exc)
    try:
        html = evidence_dir / "page.html"
        html.write_text(page.content(), encoding="utf-8")
        paths["html"] = str(html)
    except Exception as exc:
        logger.warning("Could not capture the page HTML: %s", exc)
    return paths


def delete_screenshots(job_dir: Path) -> None:
    """Remove visual evidence after a submission has been confirmed."""
    for path in job_dir.glob("*.png"):
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("Could not delete screenshot %s: %s", path, exc)


def on_form_resume(assistant, prepared):
    """The resume actually on the employer's form.

    An updated resume can be swapped into a run that is already open (via
    data/_resume_override.txt), and checking the prepared file's name against
    the form then reported the right resume as "not attached".
    """
    return getattr(assistant, "attached_resume", None) or prepared


def worth_returning_to(url: str) -> bool:
    """Whether a page is one to reopen later.

    An error page, an API endpoint or a sign-in redirect is where a run ended
    up, not where it got to: one was recorded as the application's page and
    Resume walked the browser straight back into it.
    """
    if not url or not url.startswith("http"):
        return False
    lowered = url.lower()
    return not any(mark in lowered for mark in (
        "/api/", "auth/error", "error=", "/error", "signin", "sign-in", "/login",
        "accounts.google.com", "about:blank", "/logout",
    ))


def save_stop_page(page, job_dir: Path) -> None:
    """A screenshot and the page's text where the agent stopped for the owner.

    KBI, 28 September: the run stopped on a sign-in step and left nothing to show which page it was on.
    The text goes through hide_secrets, so a typed password is never written."""
    from perception import hide_secrets
    try:
        stem = Path(job_dir) / f"stopped_{datetime.now():%Y%m%d_%H%M%S}"
        stem.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(stem.with_suffix(".png")), full_page=True)
        stem.with_suffix(".txt").write_text(
            f"{page.url}\n\n" + hide_secrets(page.locator("body").aria_snapshot(mode="ai")), encoding="utf-8")
        logger.info("Where it stopped: %s", stem.with_suffix(".png"))
    except Exception as exc:
        logger.info("Could not save the page it stopped on: %s", str(exc).splitlines()[0][:100])


def shows_the_application(assistant, page) -> bool:
    """A reopened page still holds the application: a form to fill (any frame), or the job posting.

    Judged by what is on the page, not by its address: a careers home looked like any other page."""
    try:
        page.wait_for_timeout(2_000)            # Workday draws the form after the page has loaded
        if assistant.on_job_description(page):
            return True
        for frame in page.frames:
            found = frame.evaluate("""() => [...document.querySelectorAll(
                    'input:not([type=hidden]):not([type=search]):not([type=submit]):not([type=button]), '
                    + 'textarea, select, [role=combobox], [role=radio], [role=checkbox]')]
                .some(e => !!(e.offsetParent || e.getClientRects().length))""")
            if found:
                return True
    except Exception:
        return True                             # when the page cannot be read, leave it as it was
    return False


def remember_progress(tracker, key: str, page, note: str = "", assistant=None) -> None:
    """Writes down where this application has got to.

    Called on every pass rather than only when a review package is written, so
    a run that is stopped, crashes, or has its browser closed still leaves a
    record pointing at the page it reached -- which is what Resume reopens.
    """
    try:
        url = page.url
    except Exception:
        return
    # Identity continuity and "where to resume" are different questions: a login/SSO/OTP/
    # verification host is not a page worth reopening a human to, but it is still part of this
    # same bound application's flow, and excluding it here left a gap (the owner's finding of
    # 7 October 2026) where re-entering from that exact host found no prior effect history. So
    # this is unconditional -- not gated by worth_returning_to() -- while update_last_page below
    # still is, since that one really is about where Resume should reopen the browser.
    _remember_submission_identity_alias(assistant, url)
    if not worth_returning_to(url):
        return
    if hasattr(tracker, "update_last_page"):
        try:
            tracker.update_last_page(key, url)
        except Exception as exc:
            logger.debug("Could not record the page: %s", exc)
    if note and hasattr(tracker, "record_event"):
        try:
            tracker.record_event(key, "note", f"{note}: {url}"[:400])
        except Exception as exc:
            logger.debug("Could not record the event: %s", exc)


def _remember_submission_identity_alias(assistant, url: str) -> None:
    """Durably ties a host/path visited while working a bound application (a login page, an ATS
    redirect, a review step) to that run's bound submission-effect identity, so a later invocation
    started from there finds the same effect history instead of a fresh one.

    `apply_flow.py` binds `assistant.submission_key` once, from the original posting URL, before
    any navigation; it never recomputes it from the page the browser happens to be on. This only
    records the *other* host/path as a known alias of that one stable identity -- it does not
    change what the current run authorizes."""
    if assistant is None:
        return
    store = getattr(assistant, "submission_state_store", None)
    primary = getattr(assistant, "submission_key", "")
    if store is None or not primary or not hasattr(store, "record_submission_identity_alias"):
        return
    try:
        alias = submission_effect_key_for_url(url)
    except ValueError:
        return
    try:
        store.record_submission_identity_alias(primary, alias)
    except Exception:
        logger.debug("Could not record a submission identity alias for %s", url[:80])


def remembered_answers(tracker, questions) -> dict:
    """Answers already given to the same question on an earlier application.

    Employers word screening questions differently, so the lookup matches on
    meaning (db.recall_answer). An answer the user typed themselves outranks
    one the agent drafted, and a choice that isn't among the options this
    employer offers is not reused -- that would be putting words in their
    mouth.
    """
    if not hasattr(tracker, "recall_answer"):
        return {}
    recalled: dict[str, str] = {}
    for question in questions:
        # Never reused, however it was answered before: an immigration,
        # contractual or criminal-history question is the user's to answer on
        # each form, and wording differences between employers change what is
        # being asked.
        if safety.is_legal_status_question(question.question_text) or \
                safety.is_attestation(question.question_text):
            continue
        try:
            matches = tracker.recall_answer(question.question_text, limit=5)
        except Exception as exc:
            logger.debug("Recall failed for %r: %s", question.question_text[:50], exc)
            continue
        for match in matches:
            answer = (match.get("answer") or "").strip()
            options = question.options or []
            if not answer or (options and answer not in options):
                continue
            # Only what the user answered themselves. An answer the agent
            # drafted is a judgement made from what it had at the time;
            # replaying it turns one guess into a fact repeated across
            # applications -- that is how "Have you ever held J-1 status? Yes"
            # was about to be filled in on a second employer's form.
            if match.get("answered_by") != "user":
                continue
            recalled[question.question_text] = answer
            logger.info("RECALLED: %r -> %r (answered by %s before)",
                        question.question_text[:60], answer[:40], match.get("answered_by"))
            break
    return recalled


_EVERYDAY_ANSWERS = frozenset({"yes", "no", "n/a", "na", "none", "true", "false", "not applicable"})


def learn_user_answers(assistant, page, tracker, key, profile, company: str = "") -> int:
    """Records what the user filled in by hand, so the next application answers
    it by itself.

    Only values a person entered are learned: never the agent's own, and --
    where the provenance observer ran -- never a value the site put there that
    nobody touched (a parser's guess would otherwise be remembered as the
    owner's answer and reused), and only where they are not already in the
    profile. Legal attestations and signatures are never
    stored: those are the user's to give every time.
    """
    if not hasattr(tracker, "record_answer"):
        return 0
    known = {str(v).strip().lower() for v in safety.profile_values(profile).values() if str(v).strip()}
    host = urlparse(page.url).netloc
    learned = 0
    for field in assistant.read_back_fields(page):
        label, value = (field.get("label") or "").strip(), (field.get("value") or "").strip()
        if field.get("source") in ("agent", "site") or not label or not value or len(label) < 6:
            continue   # the agent's own answer, or one the site put there that nobody touched
        # A profile detail (a name, an address) is not learned again -- but 'Yes' and 'No' are in every profile,
        # and skipping them left every yes/no question the person answered unlearned.
        if (value.lower() in known and value.lower() not in _EVERYDAY_ANSWERS)                 or safety.is_attestation(label) or safety.is_attestation(value):
            continue
        if safety.is_legal_status_question(label):
            continue  # the user answers these afresh every time
        if assistant.values.wrote_value(page, value):
            continue  # the agent's own answer, read back through another control
        try:
            tracker.record_answer(key, host, label, value, answered_by="user")
            learned += 1
        except Exception as exc:
            logger.debug("Could not remember %r: %s", label[:50], exc)
        # A general question, answered in a few words, is the person's answer for every employer: it joins
        # their saved answers, which every form is answered from and the dashboard shows and edits.
        try:
            import profile_setup
            if profile_setup.remember_answer(label, value, company):
                logger.info("SAVED ANSWER: %r = %r, for every application", label[:60], value[:40])
        except Exception as exc:
            logger.debug("Could not add %r to the saved answers: %s", label[:50], exc)
    if learned:
        logger.info("LEARNED: remembered %d answer(s) you filled in, for next time", learned)
    return learned


def hand_over(assistant, page, tracker, key, job, job_dir: Path, resume_name: str, summary_path: Path,
              config=None, profile=None, documents: Optional[dict] = None, step: int = 1) -> str:
    """The end of the agent's work on a form.

    1. validate required fields   2. detect visible errors
    3. capture screenshot, page HTML, field-by-field comparison and an audit record
    4. decide (verified auto-submit, off by default) 5. set the status
    6. raise the browser window 7. say plainly what is left for the user

    Returns the status that was set.
    """
    documents = documents or {}
    assistant.tracker, assistant.application_key = tracker, key
    if not hasattr(assistant, "submission_state_store"):
        assistant.submission_state_store = tracker
    if profile is not None:
        learn_user_answers(assistant, page, tracker, key, profile, getattr(job, "company", ""))
    report = assistant.validate_application(page, resume_name)
    form_fields = assistant.read_back_fields(page)
    # Sponsorship and work authorization, checked against the profile whoever
    # filled them in -- Schwab's form carried a "No" over from an old application.
    report["legal_answer_conflicts"] = safety.legal_answer_conflicts(form_fields, profile)
    for conflict in report["legal_answer_conflicts"]:
        logger.warning("LEGAL_ANSWER_CONFLICT: %s", conflict)
    # Did the agent actually do anything here? A status of "ready to submit"
    # has to mean a filled form, not an empty page with no errors on it.
    report["form_reached"] = bool(getattr(assistant, "_seen_application_form", False)) or bool(form_fields)
    report["fields_filled"] = sum(1 for f in form_fields if (f.get("value") or "").strip())
    report["documents_attached"] = len(
        assistant.attached_document_names(page)
        if hasattr(assistant, "attached_document_names") else []
    )
    evidence = collect_evidence(assistant, page, job_dir, step)

    # Which documents on the form are ours, verified by content, not by name.
    verified_documents = all(
        tracker.document_matches(key, kind, path)
        for kind, path in documents.items() if path
    ) if hasattr(tracker, "document_matches") and documents else False

    tracked = {}
    if hasattr(tracker, "get"):
        record = tracker.get(key)
        if record:
            tracked = {"title": record.title, "company": record.company, "url": record.url}

    approved = safety.approved_values(form_fields, profile, approved_answers_file(),
                                      getattr(assistant.values, "records", {}))
    decision = safety.evaluate_auto_submit(
        enabled=bool(getattr(config, "auto_submit_verified_only", False)),
        job={"title": job.title, "company": job.company, "url": page.url},
        tracked=tracked or {"title": job.title, "company": job.company, "url": page.url},
        report=report,
        form_fields=form_fields,
        approved=approved,
        uploaded_documents=report.get("attached_documents") or [],
        expected_documents=[Path(p).name for p in documents.values() if p],
        documents_verified=verified_documents,
        evidence_paths=evidence,
    )

    # The field-by-field comparison report, beside the screenshot it describes.
    comparison_path = Path(evidence.get("screenshot", str(job_dir / "x"))).with_name("comparison.json")
    comparison_path.write_text(json.dumps(decision.as_dict(), indent=2), encoding="utf-8")
    evidence["comparison"] = str(comparison_path)
    summary_path.with_name("validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    if hasattr(tracker, "record_event"):
        tracker.record_event(key, "auto_submit", decision.summary(),
                             screenshot_path=evidence.get("screenshot", ""),
                             html_path=evidence.get("html", ""),
                             payload=decision.as_dict())

    status, message = safety.handover_status(report)
    if decision.eligible:
        submitted = submit_verified(assistant, page, tracker, key, job, job_dir, decision)
        if submitted:
            return STATUS_SUBMITTED
        status, message = STATUS_NEEDS_USER_REVIEW, "verified auto-submit did not complete -- please check the form"
    # There is deliberately no legacy AUTO_SUBMIT fallback here. Every
    # automatic submission must pass evaluate_auto_submit() above.

    if status == STATUS_READY_TO_SUBMIT and assistant.find_submit_button(page) is None:
        # Schwab's sign-in step was reported "ready to submit": nothing left
        # to fill there, but it was not the application's last page.
        status, message = STATUS_NEEDS_USER_REVIEW, (
            "stopped before the last page: nothing is left to fill here, but the agent found no way "
            "on to the next step")
    tracker.update_status(key, status, notes=message)
    if hasattr(assistant, "human_handoff"):
        assistant.human_handoff(page)
    assistant.raise_window(page)

    banner = "=" * 78
    logger.info(banner)
    if status == STATUS_READY_TO_SUBMIT:
        logger.info("READY TO SUBMIT -- %s at %s", job.title, job.company)
        logger.info("Everything the agent can fill is filled and the form reports no errors.")
    else:
        logger.info("NEEDS YOUR REVIEW -- %s at %s", job.title, job.company)
        for item in report["required_still_blank"]:
            logger.info("  still blank : %s", item)
        for item in report["errors_shown"]:
            logger.info("  form error  : %s", item)
        for item in report.get("warnings_shown", []):
            logger.info("  warning     : %s", item)
        for item in report["attestations_pending"]:
            logger.info("  your signature/attestation: %s", item)
        for item in report.get("ambiguous_choices", []):
            logger.info("  unclear choice: %s", item)
        for item in report.get("unsupported_questions", []):
            logger.info("  no approved answer: %s", item)
        if report["captcha"]:
            logger.info("  a CAPTCHA is showing -- only you can complete it")
    if report.get("resume_attached") is False:
        logger.info("  the resume does not show as attached")
    for question in getattr(assistant, "written_answers", None) or []:
        logger.info("  written for you -- read before you submit: %s", question)
    if not decision.eligible:
        logger.info("Auto-submit: %s", decision.summary())
    logger.info("Review the form in the browser window, then click Submit yourself.")
    logger.info("Evidence: %s", ", ".join(evidence.values()) or summary_path.parent)
    logger.info(banner)
    return status


def run_page_agent(assistant, page, claude, config, profile, resume, job, tracker, key: str,
                   job_dir: Path, resume_file: Path, args, signal_path: Path,
                   experience_data: dict | None = None) -> None:
    """Works the application with the reading agent (page_agent.py).

    It reads each page, answers what the owner's facts answer, moves on, and
    submits once every check passes. When it needs the owner it says why, waits
    for them to deal with it in the browser, and carries on from that page when
    they press Continue -- or by itself once a CAPTCHA they solved is gone.
    """
    import page_agent

    made: dict = {}
    experience_data = experience_data or {}

    def cover_letter():
        if not getattr(config, "generate_cover_letters", False):
            return None
        # Task 4.3: Strict lazy compilation - only if explicit required cover letter field detected
        has_req = getattr(assistant, "has_required_cover_letter_field", None)
        if has_req and not has_req(page):
            return None
        if "letter" not in made:
            made["letter"] = prepare_cover_letter(claude, resume, job, profile, job_dir, tracker, key)
            if made["letter"]:
                tracker.update_materials(key, str(resume_file), str(made["letter"][1]))
                store_materials(tracker, key, None, made["letter"][1])
        return made["letter"]

    store_materials(tracker, key, resume_file, None)
    agent = page_agent.PageAgent(assistant, claude, config, profile, resume, job, tracker, key,
                                 job_dir, resume_file, cover_letter)
    import repeated_entries
    # each job and degree, for boxes that repeat per entry; a degree's dates completed from the profile
    agent.history = repeated_entries.with_profile(experience_data, profile)
    while True:
        try:
            outcome = agent.run(page)
        except (Exception, TimeoutError) as exc:
            logger.exception("UNHANDLED ERROR / STALL in agent.run: %s", exc)
            # What the page says now comes first: the owner may have sent it while the agent was busy (Secunetics,
            # 29 September -- "Your application was submitted successfully" showed while the run crashed, and the
            # application was recorded as needing the owner).
            if confirmed_after_all(agent, page, tracker, key):
                return
            try:
                dump_path = dump_forensic_failure(page, reason=f"Unhandled agent error/stall: {exc}", console_logs=getattr(page, "_console_logs", []))
                tracker.update_status(key, STATUS_NEEDS_USER_REVIEW, notes=f"Crashed/Stalled: {exc} (dump: {dump_path})")
            except Exception as dump_err:
                logger.error("Failed to dump forensic diagnostics: %s", dump_err)
            raise

        page = agent.tab(outcome.page)
        remember_progress(tracker, key, page, outcome.summary[:200], assistant=assistant)
        notes = "; ".join(agent.notes[:5])

        if outcome.kind == "submitted":
            try:
                page.screenshot(path=str(job_dir / "submitted_confirmation.png"), full_page=True)
            except Exception:
                pass
            delete_screenshots(job_dir)
            message = "Submitted by the agent -- the site confirmed it" + (f". Worth checking: {notes}" if notes else "")
            tracker.update_status(key, STATUS_SUBMITTED, notes=message[:1000])
            logger.info("SUBMITTED: %s at %s -- the site confirmed it", job.title, job.company)
            return
        if outcome.kind == "blocked_validation_loop":
            tracker.update_status(
                key,
                STATUS_BLOCKED_VALIDATION_LOOP,
                notes=f"Blocked: validation loop detected -- {outcome.summary}",
            )
            logger.error("BLOCKED_VALIDATION_LOOP: %s at %s -- %r", job.title, job.company, outcome.summary)
            try:
                dump_forensic_failure(page, reason=f"Validation loop: {outcome.summary}", console_logs=getattr(page, "_console_logs", []))
            except Exception as dump_err:
                logger.error("Failed to dump forensic diagnostics on validation loop: %s", dump_err)
            return
        if outcome.kind == "disqualified_policy_mismatch":
            tracker.update_status(
                key,
                STATUS_DISQUALIFIED_POLICY_MISMATCH,
                notes=f"Disqualified: policy mismatch -- {outcome.summary}",
            )
            logger.warning("DISQUALIFIED_POLICY_MISMATCH: %s at %s -- %r", job.title, job.company, outcome.summary)
            return
        if outcome.kind == "no_sponsorship":
            tracker.update_status(key, STATUS_SKIPPED, notes=f"Skipped: no visa sponsorship -- {outcome.summary}")
            logger.warning("SKIPPED: %s at %s does not sponsor visas -- %r", job.title, job.company, outcome.summary)
            return

        # Task 3.3: Enforce the Human-in-the-Loop Review Border
        is_review = assistant.is_review_step(page) or any(
            "ready for you to submit" in r or "last step" in r or "review step" in r.lower()
            for r in outcome.reasons
        )
        if is_review:
            logger.info("REVIEW BORDER: Reached designated final review step. Halting for human review.")
            assistant.save_progress(page)
            summary_path = job_dir / f"summary_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json"
            hand_over(
                assistant, page, tracker, key, job, job_dir,
                Path(resume_file).name, summary_path,
                config=config, profile=profile,
                documents={"resume": resume_file,
                           **({"cover_letter": made["letter"][1]} if "letter" in made and made["letter"] else {})},
                step=agent.pages_read,
            )
            try:
                decision = assistant.wait_for_signal(signal_path, timeout_seconds=args.timeout, page=page)
            except TimeoutError:
                logger.info("No instruction received; application left filled and unsubmitted.")
                return
            if not page.is_closed():
                agent.note_owner_changes(page)
            agent._ensure_state()
            agent.forget_sign_in_attempts(owner_acted=(decision == "continue"))
            if decision == "submitted_by_user":
                evidence = getattr(assistant, "_confirmation_evidence", "") or "the page confirmed it"
                status, note = safety.verification_status(evidence)
                tracker.update_status(key, status, notes=note)
                logger.info("SUBMITTED_BY_USER: %s", note)
                return
            if decision in RUN_ENDS:
                remember_progress(tracker, key, page, f"run ended: {decision}", assistant=assistant)
                return
            if decision in ("skip", "decline", "abort", "quit"):
                tracker.update_status(key, STATUS_SKIPPED, notes="Skipped by you")
                return
            if decision == "reload_code":
                # Resume at the review stop loads the latest code too; it was taken for Continue, and the page was
                # read again with the code the run started with (Meta, 30 September).
                assistant = load_latest_code(agent, assistant, job, experience_data)
            continue

        message = outcome.summary + (f" | worth checking: {notes}" if notes else "")
        tracker.update_status(key, STATUS_NEEDS_USER_REVIEW, notes=message[:1000])
        banner = "=" * 78
        logger.info(banner)
        logger.info("NEEDS YOU -- %s at %s", job.title, job.company)
        for reason in outcome.reasons:
            logger.info("  %s", reason)
        for note in agent.notes[:5]:
            logger.info("  worth checking: %s", note)
        if outcome.kind == "captcha":
            logger.info("Solve it in the browser window: the agent notices when it is gone and carries on by "
                        "itself (Continue on the dashboard works too).")
        elif any("still blank" in r for r in outcome.reasons):
            logger.info("Fill it in the browser window: once nothing required is blank and you stop typing, the "
                        "agent carries on by itself (Continue on the dashboard works too).")
        else:
            logger.info("Deal with it in the browser window, then press Continue on the dashboard: "
                        "the agent reads the page again and carries on.")
        save_stop_page(page, job_dir)
        logger.info(banner)
        if hasattr(assistant, "human_handoff"):
            assistant.human_handoff(page)
        assistant.raise_window(page)
        agent.remember_page_state(page)
        try:
            decision = assistant.wait_for_signal(signal_path, timeout_seconds=args.timeout, page=page,
                                                 for_captcha=outcome.kind == "captcha",
                                                 for_blanks=any("still blank" in r for r in outcome.reasons))
        except TimeoutError:
            logger.info("No instruction received; the application is left as it is, unsubmitted.")
            return
        terminal_decisions = (*RUN_ENDS, "submitted_by_user", "skip", "decline", "abort", "quit")
        if decision not in terminal_decisions and hasattr(assistant, "resume_automation"):
            assistant.resume_automation(page)
        if not page.is_closed():
            agent.note_owner_changes(page)
        refresh_answer_bank(tracker)
        agent._ensure_state()
        # Only the owner's Continue lifts a hold on a sign-in (login_guard): a code reload, a refresh
        # or a re-upload says nothing about whether the account was looked at.
        agent.forget_sign_in_attempts(owner_acted=(decision == "continue"))
        if decision == "submitted_by_user":
            evidence = getattr(assistant, "_confirmation_evidence", "") or "the page confirmed it"
            status, note = safety.verification_status(evidence)
            tracker.update_status(key, status, notes=note)
            logger.info("SUBMITTED_BY_USER: %s", note)
            return
        if decision in RUN_ENDS:
            remember_progress(tracker, key, page, f"run ended: {decision}", assistant=assistant)
            return
        if decision in ("skip", "decline", "abort", "quit"):
            tracker.update_status(key, STATUS_SKIPPED, notes="Skipped by you")
            return
        if decision == "reload_code":
            assistant = load_latest_code(agent, assistant, job, experience_data)
        elif decision == "fill_experience":
            # The reading agent normally handles ordinary profile fields.
            # Workday's repeated employment/education widget needs the
            # structured, résumé-derived dates and searchable selections.
            assistant.fill_experience_section(page, experience_data.get("experience", []))
            assistant.fill_education_section(page, experience_data.get("education", []))
            assistant.upload_resume(page, resume_file)
            logger.info("Filled Workday experience and education from the structured resume")
        elif decision == "reupload_resume":
            assistant.upload_resume(page, resume_file)
            logger.info("Retried the required resume upload")
        elif decision == "fill_education":
            assistant.fill_education_section(page, experience_data.get("education", []))
            assistant.upload_resume(page, resume_file)
            logger.info("Refilled Workday education and retried the required resume upload")
        # continue / refresh / anything else: read the page again and carry on


def submit_verified(assistant, page, tracker, key, job, job_dir: Path, decision) -> bool:
    """Submits ONLY on an eligible AutoSubmitDecision (every required field
    matched approved data, job and documents verified, nothing uncertain on the
    page), and only because the user turned AUTO_SUBMIT_VERIFIED_ONLY on.

    The evidence in `decision` was captured before this ran. Afterwards the
    application is still only recorded as submitted once the site or an email
    confirms it -- clicking is not evidence.
    """
    logger.info("AUTO_SUBMIT: %s -- submitting %s at %s", decision.summary(), job.title, job.company)
    submission_store = getattr(assistant, "submission_state_store", tracker)
    submission_key = getattr(assistant, "submission_key", key)
    identity_aliases = getattr(assistant, "submission_key_aliases", ())
    prior_state = refuse_submission_replay(
        submission_store, submission_key, "submit_gateway", aliases=identity_aliases
    )
    if prior_state:
        return False
    assistant.tracker, assistant.application_key = tracker, key
    assistant.submission_state_store = submission_store
    clicked = assistant.click_verified_submit(page, decision)
    if not clicked:
        logger.error("AUTO_SUBMIT_FAILED: the Submit button could not be clicked")
        try:
            if prior_state in (None, "AUTHORIZED") \
                    and submission_store.get_submission_effect_state(submission_key) == "DISPATCHED":
                submission_store.finish_submission_effect(submission_key, "UNCERTAIN")
        except Exception:
            logger.exception("Could not preserve uncertain submission state for %s", key[:12])
        return False
    try:
        evidence = assistant.wait_for_submission_evidence(page, job.title)
        status, note = safety.verification_status(evidence)
        outcome_state = "CONFIRMED" if evidence and status == STATUS_SUBMITTED else "UNCERTAIN"
        submission_store.finish_submission_effect(
            submission_key, outcome_state,
            evidence_kind="independent_page_or_email" if outcome_state == "CONFIRMED" else None,
        )
    except Exception:
        logger.exception("Post-dispatch result is unknown for application %s", key[:12])
        try:
            if submission_store.get_submission_effect_state(submission_key) == "DISPATCHED":
                submission_store.finish_submission_effect(submission_key, "UNCERTAIN")
        except Exception:
            logger.exception("Could not durably mark application %s uncertain", key[:12])
        tracker.update_status(key, STATUS_NEEDS_USER_REVIEW, notes="submission outcome is uncertain; reconcile before retry")
        return False
    tracker.update_status(key, status, notes=note)
    try:
        page.screenshot(path=str(job_dir / "submitted_confirmation.png"), full_page=True)
    except Exception:
        pass
    if status == STATUS_SUBMITTED:
        delete_screenshots(job_dir)
    logger.info("AUTO_SUBMIT result: %s -- %s", status, note)
    return status == STATUS_SUBMITTED


def refuse_submission_replay(
    submission_store, key: str, source: str, aliases: tuple[str, ...] | list[str] = ()
) -> Optional[str]:
    """Keep dispatch history sticky and convert an interrupted dispatch to uncertain.

    Checked identities are not only whatever the caller happened to pass: any alias durably
    recorded for `key` (a cross-host/path redirect seen earlier in this or a prior run) is
    consulted too, so a URL-derived key that changed across invocations cannot dodge an existing
    DISPATCHED/CONFIRMED/UNCERTAIN history merely because it is not literally the same string.
    """
    durable_aliases: tuple[str, ...] = ()
    if hasattr(submission_store, "submission_identity_group"):
        try:
            durable_aliases = submission_store.submission_identity_group(key)
        except Exception:
            logger.debug("Could not read durable submission identity aliases for %s", key[:12])
    candidate_keys = tuple(dict.fromkeys(
        (key, *(alias for alias in (*aliases, *durable_aliases) if alias and alias != key))
    ))
    for candidate in candidate_keys:
        state = submission_store.get_submission_effect_state(candidate)
        if state == "DISPATCHED":
            submission_store.finish_submission_effect(candidate, "UNCERTAIN")
            state = "UNCERTAIN"
        if state in {"AUTHORIZED", "UNCERTAIN", "CONFIRMED"}:
            submission_store.record_submission_safety_event(
                candidate, "SUBMISSION_REPLAY_BLOCKED", {"state": state, "source": source}
            )
            logger.error("SUBMISSION_REPLAY_BLOCKED: durable state is %s", state)
            return state
    return None


# What the owner can say, from the dashboard or by closing the window, that ends the run. "close" is the
# dashboard's "Close browser" button: it was missing here, so the flow took it for "continue" and went on
# reading pages and resetting the status after the owner had closed it (Aristocrat, 29 September).
RUN_ENDS = ("browser_closed", "left_form", "close")


def brain_kind(config) -> str:
    """Who answers the application pages under this configuration: "profile" (nobody: the saved profile),
    "gemini", "openai", "session" (the Claude Code session) or "api" (Claude). FORM_ANSWER_MODE=gemini or openai
    decides it whatever AGENT_BRAIN says."""
    mode = getattr(config, "form_answer_mode", "profile")
    if mode in ("profile", "gemini", "openai"):
        return mode
    return "session" if getattr(config, "agent_brain", "api") == "session" else "api"


def brain_for(config, job=None):
    """The document writers, and the page planner the owner chose (ai_choice.py).

    The documents always go to the owner's writers (Settings: RESUME_WRITER). The pages go to Gemini, OpenAI or
    Claude as FORM_ANSWER_MODE says, each climbing the owner's ladder of models; in profile mode the pages are
    answered from the saved profile, and in session mode the Claude Code session plans them.
    """
    documents = ai_choice.Writers(ai_choice.writers(config))
    kind = brain_kind(config)
    if kind == "profile":
        logger.info("BRAIN: form answers use the local profile planner; the writers write the documents")
        brain = ai_choice.Brain(ClaudeClient(config), documents)
    elif kind == "session":
        import session_planner
        brain = session_planner.SessionPlanner(config, folder=Path("data"), fallback=ClaudeClient(config))
        brain.now_applying = f"{getattr(job, 'company', '')} -- {getattr(job, 'title', '')}".strip(" -")
        logger.info("BRAIN: this run asks the Claude Code session about each page (no API credit used)")
    elif kind == "gemini":
        from gemini_integration import GeminiBrain
        logger.info("BRAIN: Gemini answers the application pages; the chosen writer writes the documents")
        brain = GeminiBrain(config, documents=documents)
    else:
        logger.info("BRAIN: %s answers the application pages; the chosen writer writes the documents",
                    "OpenAI" if kind == "openai" else "Claude")
        brain = ai_choice.Brain(ai_choice.answer_client(config, "openai" if kind == "openai" else "claude"),
                                documents)
    brain.made_for = kind          # what rechoose_brain compares with after a code reload
    return brain


def rechoose_brain(current, config, job=None):
    """After a code reload: the brain the settings choose now -- the same decision as at the start (brain_kind),
    never a second reading of the settings. The brain in use is kept when they still choose it (a session brain
    takes its class's new code). Mutual of Enumclaw, 29 September: a run started on the profile planner was moved
    to the Claude Code session by a reload that read AGENT_BRAIN on its own."""
    kind = brain_kind(config)
    if getattr(current, "made_for", None) == kind:
        if kind == "session":
            import session_planner
            current.__class__ = session_planner.SessionPlanner
        return current
    logger.info("BRAIN: after the reload, the settings choose '%s' for the pages", kind)
    return brain_for(config, job)


def confirmed_after_all(agent, page, tracker, key: str) -> bool:
    """After a run stopped on an error: if the page now says the application was received, it is recorded as
    submitted (by the owner -- the agent never presses the last Submit) and True is returned."""
    try:
        tab = agent.tab(page)
        if not agent._site_confirms(tab):
            return False
        said = " ".join((tab.inner_text("body", timeout=5_000) or "").split())
        status, note = safety.verification_status("the page confirmed it: " + said[:200])
        tracker.update_status(key, status, notes=note)
        logger.info("SUBMITTED_BY_USER: the page confirmed the application when the run stopped")
        return True
    except Exception as exc:
        logger.debug("Could not check for a confirmation after the error: %s", exc)
        return False


_RELOADED_ON_THEIR_OWN = {"__main__", "apply_flow", "browser_automation", "page_agent", "config"}


def project_modules_used_by(*modules) -> list:
    """The project's own modules these modules use, directly or through each other, each listed after the
    modules it uses: the order a reload must follow, or a reloaded module binds the old version of what it
    imports. The modules that are reloaded on their own (this one, the two agents, config) are left out."""
    root = Path(__file__).resolve().parent
    order, seen = [], set()

    def visit(module) -> None:
        if module.__name__ in seen:
            return
        seen.add(module.__name__)
        for value in list(vars(module).values()):
            try:
                used = value if inspect.ismodule(value) else sys.modules.get(getattr(value, "__module__", None) or "")
                path = getattr(used, "__file__", None)
                if used is not None and used is not module and path and Path(path).resolve().parent == root:
                    visit(used)
            except Exception:
                continue
        order.append(module)

    for module in modules:
        visit(module)
    return [m for m in order if m.__name__ not in _RELOADED_ON_THEIR_OWN]


def load_latest_code(agent, assistant, job, experience_data):
    """Resume on a waiting run: the agent's latest code, the owner's latest profile and settings, the same form.
    Every stop where the run waits calls this one routine (the review stop once treated Resume as Continue)."""
    import page_agent
    assistant = reload_browser_automation(assistant)
    importlib.reload(page_agent)
    agent.__class__ = page_agent.PageAgent
    agent.assistant = assistant
    # The resume bookkeeping above ran on the old code: run the new code's
    # (a loop guard that had tripped is re-armed, new state starts empty).
    agent._ensure_state()
    agent.forget_sign_in_attempts(owner_acted=False)
    # The profile, application settings and Claude's instructions too:
    # a corrected name or new authorization must reach the live run.
    try:
        agent.config = config_module.get_app_config()
        agent.profile = config_module.get_user_profile()
        assistant._profile = agent.profile
        import repeated_entries
        agent.history = repeated_entries.with_profile(experience_data, agent.profile)
    except Exception as exc:
        logger.warning("Could not reload the profile or settings: %s", exc)
    try:
        import claude_integration
        importlib.reload(claude_integration)
        if agent.claude.__class__.__name__ == "ClaudeClient":
            agent.claude.__class__ = claude_integration.ClaudeClient
    except Exception as exc:
        logger.warning("Could not reload the Claude instructions: %s", exc)
    try:
        # A run started on the API can be moved onto the session brain
        # (or back) without restarting, keeping the form as it stands.
        import session_planner
        importlib.reload(session_planner)
        agent.claude = rechoose_brain(agent.claude, config_module.get_app_config(), job)
    except Exception as exc:
        logger.warning("Could not switch the brain over: %s", exc)
    logger.info("Reloaded the reading agent, your profile and the instructions")
    return assistant


def reload_browser_automation(assistant: JobApplicationAssistant) -> JobApplicationAssistant:
    """Re-reads browser_automation.py from disk and rebinds `assistant` to
    the freshly-reloaded class, so a bug fix takes effect in THIS already
    -running process -- without closing the browser or losing the session/
    login/draft state. importlib.reload() redefines the module's classes in
    place; reassigning __class__ makes the existing instance use the new
    method implementations (Python looks up methods on the instance's
    __class__ at call time, not at instance-creation time).

    The reload is attempted only after the file compiles, and any failure is
    swallowed: a SyntaxError propagating out of importlib.reload() kills the
    process, which closes the browser and throws away a part-filled
    application and its login session. A bad edit should cost a retry, not
    the whole run."""
    path = Path(browser_automation.__file__)
    try:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    except SyntaxError as exc:
        logger.error("RELOAD_REJECTED: %s has a syntax error (line %s): %s -- keeping the running version",
                     path.name, exc.lineno, exc.msg)
        return assistant

    try:
        # The profile too: the answers in config.py are data the run uses, and
        # a fix to them was reaching the code but not the values -- the run
        # went on offering the answer it had started with.
        try:
            importlib.reload(config_module)
            fresh = config_module.get_user_profile()
            if getattr(assistant, "_profile", None) is not None:
                assistant._profile = fresh
            logger.info("Reloaded the profile as well")
        except Exception as exc:
            logger.warning("Could not reload config.py: %s", exc)

        # Every project module the running code uses (safety.py's rules, account_state's table, the fillers ...),
        # each after the modules it uses. Reloading browser_automation and page_agent alone left new code calling
        # what the old helpers do not have: a reload into Mutual of Enumclaw's waiting run (29 September) would have
        # met an account_state without its reset step.
        helpers = project_modules_used_by(*(m for m in (browser_automation, sys.modules.get("page_agent")) if m))
        for module in helpers:
            try:
                compile(Path(module.__file__).read_text(encoding="utf-8"), module.__file__, "exec")
            except SyntaxError as exc:
                logger.error("RELOAD_REJECTED: %s (line %s): %s -- keeping the running version",
                             Path(module.__file__).name, exc.lineno, exc.msg)
                return assistant
        for module in helpers:
            try:
                importlib.reload(module)
            except Exception as exc:
                logger.warning("Could not reload %s: %s", Path(module.__file__).name, exc)

        # The site adapters first: browser_automation calls hooks on them, and
        # reloading only one half left a new call meeting an old adapter --
        # which killed a live Amazon application with AttributeError.
        import sites
        for module in [sites.base] + [
            importlib.import_module(f"sites.{name}") for name in
            ("amazon", "ashby", "eightfold", "greenhouse", "lever", "successfactors", "workday")
        ]:
            try:
                compile(Path(module.__file__).read_text(encoding="utf-8"), module.__file__, "exec")
                importlib.reload(module)
            except SyntaxError as exc:
                logger.error("RELOAD_REJECTED: %s (line %s): %s", Path(module.__file__).name, exc.lineno, exc.msg)
                return assistant
        importlib.reload(sites)
        sites._CACHE.clear()  # adapters are cached per run; drop the old objects
        importlib.reload(browser_automation)
        assistant.__class__ = browser_automation.JobApplicationAssistant
        logger.info("Reloaded browser_automation.py and the site adapters in place")
    except Exception as exc:
        logger.error("RELOAD_FAILED: %s -- keeping the running version", exc)
    return assistant


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_json", help="Path to a JSON file with title/company/location/url/raw_text")
    parser.add_argument("--signal-file", required=True)
    parser.add_argument(
        "--open-url",
        help="Open this page instead of the posting -- the application form a "
             "previous run had already reached.",
    )
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Drive the whole wizard without waiting for a signal at each step. "
             "Stops at the Review step and waits there -- it never submits on "
             "its own.",
    )
    parser.add_argument(
        "--experience-json",
        help="Optional path to a JSON file with {experience: [...], education: [...]} "
             "structured resume data, used by the 'fill_experience' signal.",
    )
    args = parser.parse_args()

    experience_data: dict = {}
    if args.experience_json:
        experience_data = json.loads(Path(args.experience_json).read_text(encoding="utf-8-sig"))

    config = get_app_config()
    profile = get_user_profile()
    structured = JsonLogHandler(config.log_dir / f"run_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.jsonl")
    logging.getLogger().addHandler(structured)

    job_input = json.loads(Path(args.job_json).read_text(encoding="utf-8-sig"))
    job = build_job_description(
        title=job_input["title"], company=job_input["company"], location=job_input["location"],
        url=job_input["url"], raw_text=job_input["raw_text"],
    )
    # The questions the job site publishes for this application (Greenhouse), with their exact choices.
    job.analysis["published_questions"] = list(job_input.get("questions") or [])

    tracker = open_tracker(config)
    submission_store = JobTracker(getattr(config, "db_path", None) or config_module.DB_PATH)
    refresh_answer_bank(tracker)
    key = dedup_key_for_url(job.url)
    submission_key = submission_effect_key_for_url(job.url)
    identity_aliases = set(tracker.submission_keys_for_url(job.url))
    identity_aliases.add(key)
    identity_aliases.discard(submission_key)
    # Postgres upserts, so a re-run corrects a title/company that an earlier,
    # worse read of the posting recorded (status is never changed there). The
    # SQLite fallback has no upsert and would raise on a duplicate.
    if hasattr(tracker, "record_answer") or not tracker.is_duplicate(key):
        tracker.create(
            dedup_key=key, title=job.title, company=job.company,
            location=job.location, source_site=job.source_site, url=job.url,
        )

    effect_state = refuse_submission_replay(
        submission_store, submission_key, "application_start", aliases=tuple(identity_aliases)
    )
    if effect_state:
        logger.error("Submission for %s is parked for reconciliation", key[:12])
        return

    if hasattr(tracker, "find_submitted"):
        already = tracker.find_submitted(url=job.url, company=job.company, title=job.title)
        if already and already.dedup_key != key:
            logger.error(
                "DUPLICATE: %s at %s was already submitted on %s -- not applying again",
                already.title, already.company, already.updated_at,
            )
            return
        if already:
            logger.error("DUPLICATE: this application was already submitted -- not applying again")
            return

    resume = parse_resume(config.resume_path)
    claude = brain_for(config, job)

    # Windows forbids <>:"/\|?* in folder names; a title with '|' crashed a run.
    folder = re.sub(r'[<>:"/\\|?*]+', "_", f"{job.company}_{job.title}".replace(" ", "_")).strip("._")[:120]
    job_dir = config.output_dir / folder
    job_dir.mkdir(parents=True, exist_ok=True)

    attach_resume = prepare_materials(claude, resume, job, profile, job_dir, tracker, key)
    tracker.update_materials(key, str(attach_resume), None)
    letter: tuple[Path, Path] | None = None  # (txt, pdf), written only if a form asks for one
    letter_stored = False

    with JobApplicationAssistant(config) as assistant:
        # Lets the assistant check and record which employers have accounts.
        assistant.tracker, assistant.employer, assistant._profile = tracker, job.company, profile
        assistant.application_key = key
        assistant.submission_key = submission_key
        assistant.submission_key_aliases = tuple(identity_aliases)
        assistant.submission_state_store = submission_store
        existing = tracker.get(key) if hasattr(tracker, "get") else None
        if existing and existing.status not in ("prepared",):
            logger.info("PICKING UP: %s was last %s%s", existing.title[:50],
                        existing.status.replace("_", " "),
                        f" at {existing.last_page_url[:60]}" if getattr(existing, "last_page_url", "") else "")
            if hasattr(tracker, "record_event"):
                try:
                    tracker.record_event(key, "note",
                                         f"picked up again; was {existing.status}")
                except Exception as exc:
                    logger.debug("Could not record the pick-up: %s", exc)

        resume_at = getattr(args, "open_url", "") or ""
        if resume_at and not worth_returning_to(resume_at):
            logger.info("The page recorded for this application is not one to return to; starting from the posting")
            resume_at = ""
        if not resume_at and existing and worth_returning_to(getattr(existing, "last_page_url", "") or ""):
            # Started from the posting, but this application is already open on
            # the employer's site: carry on from there rather than walking the
            # whole wizard again.
            resume_at = existing.last_page_url
            logger.info("Continuing where the last run left it")
        if getattr(config, "agent_engine", "reader") == "reader":
            # The reading agent opens nothing on its own: it reads whatever page
            # it is given -- the posting, a sign-in, a step of the form -- and
            # works out what to do there.
            logger.info("Working the application with the reading agent")
            page = assistant.open_job_page(resume_at or job.url)
            # The browser opens behind whatever the owner is using: bring it up now, so they can watch
            # the run, not only at a hand-over.
            assistant.raise_window(page)
            page = assistant.open_embedded_form(page)
            if resume_at and not shows_the_application(assistant, page):
                # KBI's last page was recorded as the careers home (/en-US/KBI_Biopharma/): no form, no
                # posting, nothing to read, and the run stopped there. The address said nothing wrong;
                # the page does.
                logger.info("The page the last run reached no longer shows the application; "
                            "starting from the posting")
                page.goto(job.url, wait_until="domcontentloaded")
                page.wait_for_timeout(3_000)
                page = assistant.open_embedded_form(page)
            if assistant.on_job_description(page):
                page = assistant.click_apply_button(page)
                page = assistant.dismiss_apply_chooser(page)
                page = assistant.open_embedded_form(page)
            if hasattr(assistant, "autofill_from_resume"):
                assistant.autofill_from_resume(page, attach_resume)
            run_page_agent(assistant, page, claude, config, profile, resume, job, tracker, key, job_dir,
                           attach_resume, args, Path(args.signal_file), experience_data)
            return

        if resume_at:
            # Resuming: the application form itself, not the posting. Walking
            # the posting again would re-enter a wizard the last run had
            # already worked through.
            logger.info("RESUMING at %s", resume_at)
            page = assistant.open_job_page(resume_at)
            page = assistant.open_embedded_form(page)
            if assistant.on_job_description(page):
                # The address kept for this application was its posting (Schwab's
                # was), so the form still has to be opened from it.
                logger.info("This is the job posting, not the application; opening the application")
                page = assistant.click_apply_button(page)
                page = assistant.dismiss_apply_chooser(page)
        else:
            page = assistant.open_job_page(job.url)
            page = assistant.open_embedded_form(page)
            page = assistant.click_apply_button(page)
            page = assistant.dismiss_apply_chooser(page)
        page = assistant.open_embedded_form(page)

        try:
            # ATS_EMAIL is optional in .env -- the ATS account is always
            # created under the profile email, so fall back to that rather
            # than silently skipping login when only ATS_PASSWORD is set.
            login_email = config.ats_email or profile.email
            logged_in = assistant.attempt_auto_login(page, login_email, config.ats_password)
            if logged_in:
                logger.info("LOGIN_ATTEMPTED")
        except BlockedLoginDomainError as exc:
            logger.error("LOGIN_BLOCKED: %s", exc)

        signal_path = Path(args.signal_file)
        step = 0
        materials_stored = False
        filled_experience = False
        # Looking at a screenshot costs a Claude call, so a run gets a handful,
        # and never two for the same unchanged page.
        looks_left = 6
        looked_at: set[str] = set()
        apply_opened = 0
        goal = (f"Apply for the job '{job.title}' at {job.company}: reach the application form and move "
                f"through its steps. The agent fills in the applicant's details itself.")
        while True:
            step += 1
            if apply_opened < 2:
                page = assistant.open_embedded_form(page)
                if assistant.on_job_description(page):
                    apply_opened += 1
                    logger.info("Still on the job posting; opening the application")
                    page = assistant.click_apply_button(page)
                    page = assistant.dismiss_apply_chooser(page)
                    page = assistant.open_embedded_form(page)
            # Let the site's own resume parser fill what it can first; the
            # agent then only corrects and completes the rest.
            if hasattr(assistant, "autofill_from_resume"):
                assistant.autofill_from_resume(page, attach_resume)
            # Anything on the form the agent did not write is the user's own
            # answer: remember it before filling, both so it is not overwritten
            # and so the next application can use it.
            learn_user_answers(assistant, page, tracker, key, profile, getattr(job, "company", ""))
            remember_progress(tracker, key, page, assistant=assistant)

            # A job may say it will not sponsor a visa only inside its form --
            # Casey's did, on page nine. The user needs sponsorship, so the
            # application stops there and is recorded as skipped, with the
            # employer's own words as the reason.
            if getattr(profile, "requires_visa_sponsorship", False):
                disqualified, clause, shot = safety.check_visa_sponsorship_shield(
                    page, profile, job_dir=job_dir
                )
                if disqualified:
                    tracker.update_status(
                        key,
                        STATUS_DISQUALIFIED_POLICY_MISMATCH,
                        notes=f"Disqualified: policy mismatch -- {clause}",
                    )
                    logger.warning(
                        "SAFETY ABORT (DISQUALIFIED_POLICY_MISMATCH): %s at %s -- %r",
                        job.title, job.company, clause,
                    )
                    return
            fields = assistant.detect_form_fields(page)
            if not fields:
                # Dayforce shows grey placeholder bars for several seconds
                # before its form appears: give a slow form time to arrive.
                for _ in range(15):
                    if assistant.visible_input_count(page):
                        fields = assistant.detect_form_fields(page)
                        break
                    page.wait_for_timeout(1_000)
            if not fields:
                # Nothing to fill here: the page may be a chooser (Dayforce
                # offers to apply with or without an account) or a banner over
                # the form. Clearing it costs a moment and recovers a run that
                # would otherwise stop with an empty application.
                page = assistant.dismiss_apply_chooser(page)
                assistant.accept_consent_dialog(page)
                fields = assistant.detect_form_fields(page)
            filled_fields = assistant.fill_detected_fields(page, fields, profile)
            if filled_fields or len(fields) >= 4:
                # On the application now: from here no page counts as the
                # posting, so an "Apply" that submits is never pressed as one.
                assistant._form_filled_this_run = True

            questions = assistant.detect_screening_questions(page, fields)
            answers: dict[str, str] = remembered_answers(tracker, questions)
            unanswered = [q for q in questions if q.question_text not in answers]
            if unanswered:
                try:
                    answers.update(claude.answer_screening_questions(
                        resume, job, profile,
                        [{"question_text": q.question_text, "input_type": q.input_type, "options": q.options} for q in unanswered],
                    ))
                except ClaudeIntegrationError as exc:
                    logger.error("SCREENING_QA_FAILED: %s", exc)
            assistant.fill_screening_answers(page, questions, answers)

            assistant.upload_resume(page, attach_resume)

            if not materials_stored:
                store_materials(tracker, key, attach_resume, None)
                materials_stored = True

            # Task 4.3: Strict lazy compilation - only when this page has an explicit, required field for one
            has_req_cl = getattr(assistant, "has_required_cover_letter_field", getattr(assistant, "has_cover_letter_field", None))
            if has_req_cl and has_req_cl(page):
                if letter is None:
                    letter = prepare_cover_letter(claude, resume, job, profile, job_dir, tracker, key)
                    if letter:
                        tracker.update_materials(key, str(attach_resume), str(letter[1]))
                if letter and assistant.attach_cover_letter(page, *letter) and not letter_stored:
                    store_materials(tracker, key, None, letter[1])
                    letter_stored = True

            fields_summary = [
                {"label": f.label_text, "field": f.matched_profile_key}
                for f in filled_fields
            ]
            unfilled_matches = [
                {"label": f.label_text, "field": f.matched_profile_key}
                for f in fields if f.matched_profile_key and f not in filled_fields
            ]
            questions_summary = [
                {
                    "question": q.question_text,
                    "type": q.input_type,
                    "options": q.options,
                    "drafted_answer": answers.get(q.question_text, "(NOT ANSWERED -- needs your input)"),
                }
                for q in questions
            ]

            step_dir = job_dir / f"step_{step}"
            summary_path = assistant.save_review_package(
                page, step_dir, fields_summary, questions_summary, unfilled_matches
            )
            current = tracker.get(key) if hasattr(tracker, "get") else None
            if not current or current.status != STATUS_SUBMITTED:
                tracker.update_status(key, STATUS_FORM_FILLED)

            # Keep a record of every employer-specific question and what went
            # in, with the options that employer offered. Next time a form asks
            # something similar, recall_answer() can find it instead of the
            # user being asked again.
            if hasattr(tracker, "record_answer"):
                host = urlparse(page.url).netloc
                for q in questions:
                    answer = answers.get(q.question_text)
                    if not answer:
                        continue
                    try:
                        tracker.record_answer(
                            key, host, q.question_text, answer,
                            options=q.options or None, answered_by="claude",
                        )
                    except Exception as exc:
                        logger.warning("Could not record answer: %s", exc)
            logger.info("REVIEW_READY step=%d page=%s: %s", step, page.url, summary_path)
            # Where this application actually is, so Resume reopens the
            # part-filled form rather than starting from the posting again.
            if hasattr(tracker, "update_last_page"):
                try:
                    tracker.update_last_page(key, page.url)
                except Exception as exc:
                    logger.debug("Could not record the page: %s", exc)

            if args.auto:
                decision = decide_next_step(assistant, page, step, experience_data, filled_experience)
                if decision == "fill_experience":
                    filled_experience = True
                if decision == "stop" and looks_left > 0 and not assistant.is_review_step(page) \
                        and not safety.captcha_visible(page) \
                        and (assistant.find_submit_button(page) is None or assistant.on_job_description(page)):
                    seen_before = assistant._page_fingerprint(page)
                    if seen_before not in looked_at:
                        looked_at.add(seen_before)
                        looks_left -= 1
                        logger.info("No way forward found by reading the page; looking at it instead")
                        tabs_before = len(page.context.pages)
                        kind, clicked = assistant.look_and_act(page, claude, goal)
                        if clicked:
                            if len(page.context.pages) > tabs_before:
                                page = page.context.pages[-1]  # it opened in a new tab
                                try:
                                    page.wait_for_load_state("domcontentloaded", timeout=30_000)
                                except Exception:
                                    pass
                            page = assistant.dismiss_apply_chooser(page)
                            continue
                if decision == "stop":
                    assistant.save_progress(page)
                    hand_over(assistant, page, tracker, key, job, job_dir,
                              Path(on_form_resume(assistant, attach_resume)).name, summary_path,
                              config=config, profile=profile,
                              documents={"resume": on_form_resume(assistant, attach_resume),
                                         **({"cover_letter": letter[1]} if letter else {})},
                              step=step)
                    try:
                        decision = assistant.wait_for_signal(signal_path, timeout_seconds=args.timeout, page=page)
                    except TimeoutError:
                        logger.info("No instruction received; the application is left filled and unsubmitted.")
                        return
                else:
                    logger.info("AUTO step=%d -> %s", step, decision)
            else:
                try:
                    decision = assistant.wait_for_signal(signal_path, timeout_seconds=args.timeout, page=page)
                except TimeoutError as exc:
                    logger.error("TIMEOUT: %s", exc)
                    return

            if decision == "submitted_by_user":
                # The user submitted it themselves and the site (or their
                # inbox) confirmed it.
                try:
                    page.screenshot(path=str(job_dir / "submitted_confirmation.png"), full_page=True)
                except Exception as exc:
                    logger.warning("Could not capture confirmation screenshot: %s", exc)
                if status == STATUS_SUBMITTED:
                    delete_screenshots(job_dir)
                evidence = getattr(assistant, "_confirmation_evidence", "") or \
                    "the page confirmed it (see submitted_confirmation.png)"
                status, note = safety.verification_status(evidence)
                tracker.update_status(key, status, notes=note)
                remember_progress(tracker, key, page, "submitted by the user", assistant=assistant)
                logger.info("SUBMITTED_BY_USER: confirmation seen on %s", page.url)
                return

            if decision in RUN_ENDS:
                remember_progress(tracker, key, page, f"run ended: {decision}", assistant=assistant)
                # The application left the screen without any confirmation. It
                # may or may not have gone through, so it is never recorded as
                # submitted -- it goes to the user to check.
                if getattr(assistant, "_seen_application_form", False):
                    status, note = safety.verification_status(None)
                    tracker.update_status(key, status, notes=note)
                    logger.warning("NEEDS_USER_REVIEW: %s", note)
                else:
                    logger.info("%s: the application form was never reached; status left as it was",
                                decision.upper())
                return

            if decision == "reload_code":
                assistant = reload_browser_automation(assistant)
                step -= 1  # this iteration didn't actually do anything yet
                continue

            if decision == "submit":
                # Kept only to answer it: the agent stops before Submit, and
                # clicking it is the user's decision made in the browser.
                assistant.refuse_to_submit("a 'submit' signal was written")
                if hasattr(assistant, "human_handoff"):
                    assistant.human_handoff(page)
                assistant.raise_window(page)
                continue

            if decision == "continue":
                advanced = assistant.click_next_step(page)
                if not advanced:
                    if getattr(assistant, "_stuck_on", "") == "BLOCKED_VALIDATION_LOOP":
                        tracker.update_status(key, STATUS_BLOCKED_VALIDATION_LOOP, notes="Circuit breaker tripped: validation loop detected")
                        logger.error("BLOCKED_VALIDATION_LOOP: circuit breaker tripped in wizard")
                        return
                    logger.warning("NO_NEXT_BUTTON: nothing to advance to from step %d -- treat this as the final step", step)
                continue

            if decision == "fill_experience":
                assistant.fill_experience_section(page, experience_data.get("experience", []))
                assistant.fill_education_section(page, experience_data.get("education", []))
                continue

            if decision == "fix_experience":
                # Clean slate: delete whatever's currently there (bounded so
                # deletion can't wander into a later section), then refill
                # with the Add-button-text-filter fix in place.
                assistant.delete_all_entries(page, "Work Experience", "Education")
                assistant.delete_all_entries(page, "Education", "Certifications")
                assistant.fill_experience_section(page, experience_data.get("experience", []))
                assistant.fill_education_section(page, experience_data.get("education", []))
                continue

            if decision == "refresh":
                # The human already advanced the page manually (e.g. they
                # clicked a Create Account / Continue button themselves) --
                # just re-detect the current page, don't click anything.
                continue

            if decision in ("skip", "decline", "abort", "quit"):
                tracker.update_status(key, STATUS_SKIPPED, notes=f"User declined at chat review, step {step}")
                logger.info("DECLINED")
                return

            # Anything unrecognised re-detects instead of quitting. Abandoning
            # a half-filled application (and the logged-in browser session)
            # should take an explicit 'skip', never a typo or a stray BOM.
            logger.warning("UNKNOWN_SIGNAL %r -- re-detecting page instead of declining", decision)
            continue


if __name__ == "__main__":
    main()
