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
from jd_analyzer import build_job_description, dedup_key_for_url
import safety
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


def open_tracker(config):
    """Postgres when it's up, SQLite otherwise.

    Falling back rather than raising is deliberate: the tracker is a record of
    what happened, and a stopped container must never take down a live
    application mid-form. Both backends expose the same methods."""
    try:
        from db import get_tracker
        tracker = get_tracker()
        logger.info("Tracking to Postgres")
        return tracker
    except Exception as exc:
        logger.warning(
            "Postgres unavailable (%s) -- falling back to SQLite at %s. "
            "Start it with `docker compose up -d`.",
            str(exc).splitlines()[0][:120], config.db_path,
        )
        return JobTracker(config.db_path)


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
    path = job_dir / doc["filename"]
    content = bytes(doc["content"])
    if not path.is_file() or path.read_bytes() != content:
        path.write_bytes(content)
    return path


def prepare_materials(claude, resume, job, profile, job_dir: Path, tracker=None, key: str = "") -> Path:
    """Returns the resume PDF to attach for THIS job: the one already generated
    for the posting if there is one (from the database), otherwise a newly
    tailored one. The cover letter is NOT written here -- see prepare_cover_letter.

    Without this the flow attached whatever `_resume_override.txt` happened to
    point at -- a single global file left over from the previous application,
    which is how one employer's tailored resume nearly went to another. Each
    job now gets its own PDF under its own output folder.

    Falls back to the generic resume if tailoring fails; sending the untailored
    resume is a worse application, but sending the wrong company's is worse
    still, and sending nothing wastes the run."""
    generic = Path(config_module.RESUME_PATH)
    safe_company = re.sub(r"[^A-Za-z0-9]+", "", job.company)[:24] or "Job"
    reused = reuse_stored_document(tracker, key, "resume", job_dir)
    if reused is not None and reused.name not in (generic.name, f"Yaswanth_Jonnalagadda_Resume_{safe_company}.pdf"):
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
    resume_pdf = job_dir / f"Yaswanth_Jonnalagadda_Resume_{safe_company}.pdf"

    try:
        logger.info("Tailoring resume for %s...", job.company)
        raw = claude.tailor_resume(resume, job, profile)
        tailored = ensure_resume_header(
            normalise_contact_details(strip_model_preamble(raw, profile), profile),
            profile, job,
        )
        # The user's decision (2026-09-15): always send Claude's tailored
        # resume. If a draft names something the real resume doesn't, it is
        # rewritten once with that pointed out -- quietly, and the tailored
        # resume is used either way.
        unsupported = safety.unsupported_claims(resume.raw_text, tailored)
        if unsupported:
            logger.warning("TAILORING_RETRY: it claimed %s, which isn't in the resume",
                           ", ".join(unsupported[:6]))
            try:
                retry = claude.tailor_resume(
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
            except Exception as exc:
                logger.warning("TAILORING_RETRY failed (%s) -- keeping the first draft", exc)
        resume_txt.write_text(tailored, encoding="utf-8")
        build_resume_pdf(resume_txt, resume_pdf)
        logger.info("Tailored resume written to %s", resume_pdf.name)
        return resume_pdf
    except Exception as exc:
        logger.error("TAILORING_FAILED (%s) -- attaching the generic resume instead", exc)
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

    safe_company = re.sub(r"[^A-Za-z0-9]+", "", job.company)[:24] or "Job"
    letter_txt = job_dir / f"Yaswanth_Jonnalagadda_Cover_Letter_{safe_company}.txt"
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
    if assistant.is_review_step(page):
        return "stop"

    # Work experience and education are only offered on the My Experience
    # step, and only need filling once.
    if not filled_experience and experience_data and assistant.has_experience_section(page):
        return "fill_experience"

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


def remember_progress(tracker, key: str, page, note: str = "") -> None:
    """Writes down where this application has got to.

    Called on every pass rather than only when a review package is written, so
    a run that is stopped, crashes, or has its browser closed still leaves a
    record pointing at the page it reached -- which is what Resume reopens.
    """
    try:
        url = page.url
    except Exception:
        return
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


def learn_user_answers(assistant, page, tracker, key, profile) -> int:
    """Records what the user filled in by hand, so the next application answers
    it by itself.

    Only values the agent did not write are learned, and only where they are
    not already in the profile. Legal attestations and signatures are never
    stored: those are the user's to give every time.
    """
    if not hasattr(tracker, "record_answer"):
        return 0
    known = {str(v).strip().lower() for v in safety.profile_values(profile).values() if str(v).strip()}
    host = urlparse(page.url).netloc
    learned = 0
    for field in assistant.read_back_fields(page):
        label, value = (field.get("label") or "").strip(), (field.get("value") or "").strip()
        if field.get("source") == "agent" or not label or not value or len(label) < 6:
            continue
        if value.lower() in known or safety.is_attestation(label) or safety.is_attestation(value):
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
    if profile is not None:
        learn_user_answers(assistant, page, tracker, key, profile)
    report = assistant.validate_application(page, resume_name)
    form_fields = assistant.read_back_fields(page)
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
    elif getattr(config, "auto_submit", False) and status == STATUS_READY_TO_SUBMIT:
        resume_on_form = bool(getattr(assistant, "attached_resume", None)) or report.get("resume_attached") is True
        go, why = safety.ready_to_auto_submit(report, resume_on_form,
                                               assistant.find_submit_button(page) is not None)
        if go:
            logger.info("AUTO_SUBMIT: %s -- submitting %s at %s", why, job.title, job.company)
            if hasattr(tracker, "record_event"):
                tracker.record_event(key, "auto_submit", f"submitting: {why}",
                                     screenshot_path=evidence.get("screenshot", ""),
                                     html_path=evidence.get("html", ""))
            if assistant.click_verified_submit(page):
                found = assistant.wait_for_submission_evidence(page, job.title)
                status, note = safety.verification_status(found)
                tracker.update_status(key, status, notes=note)
                try:
                    page.screenshot(path=str(job_dir / "submitted_confirmation.png"), full_page=True)
                except Exception:
                    pass
                logger.info("AUTO_SUBMIT result: %s -- %s", status, note)
                if status == STATUS_SUBMITTED:
                    return STATUS_SUBMITTED
                message = note
            else:
                status, message = STATUS_NEEDS_USER_REVIEW, "the agent could not press Submit -- please check the form"
        else:
            logger.info("AUTO_SUBMIT: not yet -- %s", why)

    if status == STATUS_READY_TO_SUBMIT and assistant.find_submit_button(page) is None:
        # Schwab's sign-in step was reported "ready to submit": nothing left
        # to fill there, but it was not the application's last page.
        status, message = STATUS_NEEDS_USER_REVIEW, (
            "stopped before the last page: nothing is left to fill here, but the agent found no way "
            "on to the next step")
    tracker.update_status(key, status, notes=message)
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
    if not decision.eligible:
        logger.info("Auto-submit: %s", decision.summary())
    logger.info("Review the form in the browser window, then click Submit yourself.")
    logger.info("Evidence: %s", ", ".join(evidence.values()) or summary_path.parent)
    logger.info(banner)
    return status


def submit_verified(assistant, page, tracker, key, job, job_dir: Path, decision) -> bool:
    """Submits ONLY on an eligible AutoSubmitDecision (every required field
    matched approved data, job and documents verified, nothing uncertain on the
    page), and only because the user turned AUTO_SUBMIT_VERIFIED_ONLY on.

    The evidence in `decision` was captured before this ran. Afterwards the
    application is still only recorded as submitted once the site or an email
    confirms it -- clicking is not evidence.
    """
    logger.info("AUTO_SUBMIT: %s -- submitting %s at %s", decision.summary(), job.title, job.company)
    if hasattr(tracker, "record_event"):
        tracker.record_event(key, "auto_submit", "clicked Submit after verification",
                             payload={"reasons": [], "evidence": decision.evidence_paths})
    clicked = assistant.click_verified_submit(page)
    if not clicked:
        logger.error("AUTO_SUBMIT_FAILED: the Submit button could not be clicked")
        return False
    evidence = assistant.wait_for_submission_evidence(page, job.title)
    status, note = safety.verification_status(evidence)
    tracker.update_status(key, status, notes=note)
    try:
        page.screenshot(path=str(job_dir / "submitted_confirmation.png"), full_page=True)
    except Exception:
        pass
    logger.info("AUTO_SUBMIT result: %s -- %s", status, note)
    return status == STATUS_SUBMITTED


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

        # The site adapters first: browser_automation calls hooks on them, and
        # reloading only one half left a new call meeting an old adapter --
        # which killed a live Amazon application with AttributeError.
        import sites
        # safety.py holds the rules the run applies -- a fix to them reached
        # the code on disk but not the running process, which went on using
        # the version it started with.
        try:
            importlib.reload(safety)
        except Exception as exc:
            logger.warning("Could not reload safety.py: %s", exc)
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

    tracker = open_tracker(config)
    key = dedup_key_for_url(job.url)
    # Postgres upserts, so a re-run corrects a title/company that an earlier,
    # worse read of the posting recorded (status is never changed there). The
    # SQLite fallback has no upsert and would raise on a duplicate.
    if hasattr(tracker, "record_answer") or not tracker.is_duplicate(key):
        tracker.create(
            dedup_key=key, title=job.title, company=job.company,
            location=job.location, source_site=job.source_site, url=job.url,
        )

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
    claude = ClaudeClient(config)

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
            learn_user_answers(assistant, page, tracker, key, profile)
            remember_progress(tracker, key, page)

            # A job may say it will not sponsor a visa only inside its form --
            # Casey's did, on page nine. The user needs sponsorship, so the
            # application stops there and is recorded as skipped, with the
            # employer's own words as the reason.
            if getattr(profile, "requires_visa_sponsorship", False):
                try:
                    said = safety.no_sponsorship_statement(page.inner_text("body", timeout=5_000))
                except Exception:
                    said = ""
                if said:
                    tracker.update_status(key, STATUS_SKIPPED, notes=f"Skipped: no visa sponsorship -- {said}")
                    logger.warning("SKIPPED: %s at %s does not sponsor visas -- %r", job.title, job.company, said)
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

            # Cover letter only when this page has a field for one. It is
            # recorded as sent only once it is actually on the form.
            if hasattr(assistant, "has_cover_letter_field") and assistant.has_cover_letter_field(page):
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
                evidence = getattr(assistant, "_confirmation_evidence", "") or \
                    "the page confirmed it (see submitted_confirmation.png)"
                status, note = safety.verification_status(evidence)
                tracker.update_status(key, status, notes=note)
                remember_progress(tracker, key, page, "submitted by the user")
                logger.info("SUBMITTED_BY_USER: confirmation seen on %s", page.url)
                return

            if decision in ("browser_closed", "left_form"):
                remember_progress(tracker, key, page, f"run ended: {decision}")
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
                assistant.raise_window(page)
                continue

            if decision == "continue":
                advanced = assistant.click_next_step(page)
                if not advanced:
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
