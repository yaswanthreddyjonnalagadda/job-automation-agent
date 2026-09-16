"""
All Claude API calls: resume structuring, JD analysis, resume tailoring, and
cover letter generation. Centralized here so retry/error handling and prompt
design live in one place.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import anthropic

from config import AppConfig, UserProfile
from jd_analyzer import JobDescription
from resume_parser import ResumeData

logger = logging.getLogger(__name__)


class ClaudeIntegrationError(RuntimeError):
    pass


class ClaudeClient:
    def __init__(self, config: AppConfig):
        self._config = config
        self._client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    def _call(self, *, system: str, user_message: str, max_tokens: int = 2000) -> str:
        last_error: Exception | None = None
        for attempt in range(1, self._config.claude_max_retries + 1):
            try:
                response = self._client.messages.create(
                    model=self._config.anthropic_model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user_message}],
                    timeout=self._config.claude_request_timeout,
                )
                return "".join(
                    block.text for block in response.content if block.type == "text"
                )
            except anthropic.RateLimitError as exc:
                wait = 2 ** attempt
                logger.warning("Rate limited by Claude API, retrying in %ds", wait)
                time.sleep(wait)
                last_error = exc
            except anthropic.APIStatusError as exc:
                logger.error("Claude API error (status=%s): %s", exc.status_code, exc.message)
                last_error = exc
                if exc.status_code and exc.status_code < 500:
                    break
                time.sleep(2 ** attempt)
            except anthropic.APIConnectionError as exc:
                logger.warning("Connection error talking to Claude API: %s", exc)
                last_error = exc
                time.sleep(2 ** attempt)

        raise ClaudeIntegrationError(f"Claude API call failed after retries: {last_error}")

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ClaudeIntegrationError(f"Expected JSON from Claude, got: {text[:300]}") from exc

    # ------------------------------------------------------------------
    # Resume structuring
    # ------------------------------------------------------------------
    def structure_resume(self, resume: ResumeData) -> dict[str, Any]:
        system = (
            "You extract structured data from resumes. Respond with ONLY valid JSON, "
            "no markdown fences, no commentary."
        )
        user_message = (
            "Extract the following fields from this resume as JSON: "
            "contact (name, email, phone, location), summary, skills (list), "
            "experience (list of {company, title, start_date, end_date, bullets[]}), "
            "education (list of {school, degree, field, year}), certifications (list).\n\n"
            f"RESUME TEXT:\n{resume.raw_text}"
        )
        raw = self._call(system=system, user_message=user_message, max_tokens=3000)
        structured = self._extract_json(raw)
        resume.structured = structured
        return structured

    # ------------------------------------------------------------------
    # Job description analysis
    # ------------------------------------------------------------------
    def analyze_job(self, job: JobDescription) -> dict[str, Any]:
        system = (
            "You analyze job postings for a job-seeker. Respond with ONLY valid JSON, "
            "no markdown fences, no commentary."
        )
        user_message = (
            "Analyze this job posting as JSON with fields: "
            "required_skills (list), preferred_skills (list), seniority_level, "
            "key_responsibilities (list), ats_keywords (list of exact phrases to mirror "
            "in a resume), visa_sponsorship_mentioned (bool: true only if the posting "
            "explicitly says sponsorship is available), "
            "citizenship_or_clearance_required (bool: true if the posting requires US "
            "citizenship, permanent residency, or a security clearance), "
            "min_years_experience_required (integer, or null if not stated), "
            "estimated_fit_notes (string).\n\n"
            f"JOB TITLE: {job.title}\nCOMPANY: {job.company}\n\n"
            f"JOB DESCRIPTION:\n{job.raw_text}"
        )
        raw = self._call(system=system, user_message=user_message, max_tokens=2000)
        analysis = self._extract_json(raw)
        job.analysis.update(analysis)
        return analysis

    # ------------------------------------------------------------------
    # Resume tailoring
    # ------------------------------------------------------------------
    def tailor_resume(
        self, resume: ResumeData, job: JobDescription, profile: UserProfile,
        extra_instruction: str = "",
    ) -> str:
        system = (
            "You are an expert resume writer for network/cloud engineering roles. "
            "Tailor the candidate's existing resume content to the target job without "
            "fabricating experience, employers, dates, or skills the candidate does not "
            "have. Reorder and rephrase truthfully, mirror the job's terminology where "
            "it genuinely matches the candidate's background, and keep it truthful and "
            "verifiable.\n\n"
            "Output ONLY the resume itself, as plain text, starting with the "
            "candidate's name, then a headline line, then a contact line. No "
            "preamble, no commentary, no notes to the reader, no explanation of "
            "what you changed or what the candidate lacks, and no '---' "
            "separators. Anything you write is rendered verbatim into the PDF "
            "that goes to the employer, so a sentence addressed to the candidate "
            "would be printed on their resume. If the candidate lacks something "
            "the job asks for, simply leave it out."
        )
        # Build a comprehensive profile summary with ALL available fields
        # so Claude can answer simple dropdowns like "country code", "how did you hear?"
        profile_summary = f"""CANDIDATE PROFILE:
Name: {profile.full_name}
Email: {profile.email}
Phone: {profile.phone}
Phone Country Code: {profile.phone_country_code}
Current Location: {profile.current_location}
Years of experience: {profile.years_experience}
Visa sponsorship required: {profile.requires_visa_sponsorship}
Work authorization: {profile.work_authorization}
Open to relocation: {profile.open_to_relocation}
Willing to travel: {profile.willing_to_travel}
Preferred work arrangements: {", ".join(profile.work_arrangements) if profile.work_arrangements else "Not specified"}
Preferred contact method: {profile.preferred_contact_method}
Preferred language: {profile.preferred_language}
How heard about job: {profile.how_did_you_hear}
Security clearance: {profile.security_clearance}
Citizenship: {profile.country_of_citizenship}
Veteran status: {profile.veteran_status}
Disability status: {profile.disability_status}
Ethnicity: {profile.ethnicity}
Gender: {profile.gender}
Availability to start: {profile.availability_to_start}
"""
        user_message = (
            f"{profile_summary}\n"
            f"CANDIDATE RESUME:\n{resume.raw_text}\n\n"
            f"JOB: {job.title} at {job.company}\n\n"
            f"SCREENING QUESTIONS (JSON):\n{json.dumps(questions, indent=2)}"
        )
        if extra_instruction:
            # A second attempt after unsupported claims were found: name them
            # so the rewrite drops them rather than inventing new ones.
            user_message += f"\n\nIMPORTANT CORRECTION:\n{extra_instruction}"
        return self._call(system=system, user_message=user_message, max_tokens=6000)

    # ------------------------------------------------------------------
    # Screening / technical question answering
    # ------------------------------------------------------------------
    def answer_screening_questions(
        self,
        resume: ResumeData,
        job: JobDescription,
        profile: UserProfile,
        questions: list[dict[str, Any]],
    ) -> dict[str, str]:
        """questions: list of {"question_text", "input_type", "options"}
        (as produced by browser_automation.DetectedQuestion). Returns a dict
        keyed by question_text -- ONLY for questions Claude can answer
        truthfully from the resume/profile; anything it can't ground in
        real data is omitted so a human fills it in by hand instead of
        getting a fabricated answer submitted."""
        if not questions:
            return {}
        # Legal declarations and signatures are never answered for the user --
        # not even truthfully. They are dropped before Claude sees them.
        import safety
        questions = [q for q in questions
                     if not safety.is_attestation(q.get("question_text", ""))
                     and not safety.is_legal_status_question(q.get("question_text", ""))]
        if not questions:
            return {}

        system = (
            "You fill out job application screening questions on behalf of a real "
            "candidate. Answer ONLY using facts present in their resume or profile "
            "below. If a question cannot be answered truthfully from that material "
            "(e.g. it asks about something not in the resume, or requires a "
            "subjective legal declaration like work authorization you're not certain "
            "of), OMIT it from your JSON response entirely rather than guessing or "
            "fabricating -- a human will fill those in by hand. For 'select' or "
            "'radio' questions, your answer text must exactly match one of the given "
            "options. Respond with ONLY a JSON object mapping each question_text you "
            "can answer to its answer string, no markdown fences, no commentary."
        )
        # Build a comprehensive profile summary with ALL available fields
        # so Claude can answer simple dropdowns like "country code", "how did you hear?"
        profile_summary = f"""CANDIDATE PROFILE:
Name: {profile.full_name}
Email: {profile.email}
Phone: {profile.phone}
Phone Country Code: {profile.phone_country_code}
Current Location: {profile.current_location}
Years of experience: {profile.years_experience}
Visa sponsorship required: {profile.requires_visa_sponsorship}
Work authorization: {profile.work_authorization}
Open to relocation: {profile.open_to_relocation}
Willing to travel: {profile.willing_to_travel}
Preferred work arrangements: {", ".join(profile.work_arrangements) if profile.work_arrangements else "Not specified"}
Preferred contact method: {profile.preferred_contact_method}
Preferred language: {profile.preferred_language}
How heard about job: {profile.how_did_you_hear}
Security clearance: {profile.security_clearance}
Citizenship: {profile.country_of_citizenship}
Veteran status: {profile.veteran_status}
Disability status: {profile.disability_status}
Ethnicity: {profile.ethnicity}
Gender: {profile.gender}
Availability to start: {profile.availability_to_start}
"""
        user_message = (
            f"{profile_summary}\n"
            f"CANDIDATE RESUME:\n{resume.raw_text}\n\n"
            f"JOB: {job.title} at {job.company}\n\n"
            f"SCREENING QUESTIONS (JSON):\n{json.dumps(questions, indent=2)}"
        )
        raw = self._call(system=system, user_message=user_message, max_tokens=2000)
        answers = self._extract_json(raw)
        # A select/radio answer that isn't one of the offered options would be
        # a guess; drop it and let the user answer instead.
        by_text = {q.get("question_text", ""): q for q in questions}
        checked: dict[str, str] = {}
        for question_text, answer in (answers or {}).items():
            options = (by_text.get(question_text, {}) or {}).get("options") or []
            if options and answer not in options:
                logger.warning("Dropping %r -- %r is not one of the offered options",
                               question_text[:60], str(answer)[:40])
                continue
            checked[question_text] = answer
        return checked

    def choose_option(
        self, intended_value: str, options: list[str], question: str = ""
    ) -> dict[str, Any]:
        """Maps a value we hold onto whichever of a dropdown's actual options
        means the same thing -- 'Masters of Science' -> 'Masters' where that
        tenant offers only coarse labels.

        This exists so a new employer's vocabulary doesn't require a code
        change: the options are read off the live page and matched here.

        Returns {"choice": <exact option text or "">, "equivalent": bool,
        "reason": str}. `equivalent` is False when the closest option would
        change what is being asserted rather than just reword it; callers
        must not submit a non-equivalent choice without a human deciding.
        """
        if not options or not intended_value:
            return {"choice": "", "equivalent": False, "reason": "nothing to match"}

        system = (
            "You map a value onto a dropdown option on a real job application. "
            "Return the option that means the SAME THING as the intended value, "
            "differing only in wording or granularity (e.g. 'Masters of Science' "
            "and 'Masters' are the same qualification level; 'Company Career Site' "
            "and 'Corporate Website' are the same source).\n\n"
            "Set equivalent=false, and choice to the empty string, whenever the "
            "nearest option would CHANGE the claim rather than reword it. That "
            "includes: a different factual or legal status (veteran status, "
            "criminal history, work authorization, disability); a yes/no answer "
            "where the options instead demand a degree or amount of commitment; "
            "or anything that would overstate a qualification. A wrong answer here "
            "becomes a false statement on someone's real job application, so "
            "refusing is always better than approximating.\n\n"
            "Respond with ONLY a JSON object: "
            '{"choice": "<exact option text, copied verbatim, or empty string>", '
            '"equivalent": true|false, "reason": "<one short sentence>"}'
        )
        user_message = (
            f"QUESTION ON THE FORM: {question or '(not captured)'}\n"
            f"INTENDED VALUE: {intended_value}\n"
            f"AVAILABLE OPTIONS (choose verbatim from this list):\n"
            f"{json.dumps(options, indent=2)}"
        )
        raw = self._call(system=system, user_message=user_message, max_tokens=500)
        result = self._extract_json(raw)

        choice = (result.get("choice") or "").strip()
        # Never trust a returned string that isn't actually on the page.
        if choice and choice not in options:
            match = next((o for o in options if o.strip().lower() == choice.lower()), "")
            choice = match
        return {
            "choice": choice,
            "equivalent": bool(result.get("equivalent")) and bool(choice),
            "reason": (result.get("reason") or "").strip(),
        }

    # ------------------------------------------------------------------
    # Cover letter generation
    # ------------------------------------------------------------------
    def generate_cover_letter(
        self, resume: ResumeData, job: JobDescription, profile: UserProfile
    ) -> str:
        system = (
            "You write concise, specific, non-generic cover letters (under 350 words) "
            "grounded strictly in the candidate's real resume content. Never invent "
            "experience, employers, or credentials."
        )
        user_message = (
            f"CANDIDATE: {profile.full_name} ({profile.email})\n"
            f"CANDIDATE RESUME:\n{resume.raw_text}\n\n"
            f"TARGET JOB: {job.title} at {job.company}\n"
            f"JOB DESCRIPTION:\n{job.raw_text}\n\n"
            "Write a cover letter for this candidate applying to this job."
        )
        return self._call(system=system, user_message=user_message, max_tokens=1200)
