"""
All Claude API calls: resume structuring, JD analysis, resume tailoring, and
cover letter generation. Centralized here so retry/error handling and prompt
design live in one place.
"""

from __future__ import annotations

import json
import re
import logging
import time
from typing import Any

import anthropic

from config import AppConfig, UserProfile
from jd_analyzer import JobDescription
from resume_parser import ResumeData

logger = logging.getLogger(__name__)


def explain(exc: Exception) -> str:
    """What went wrong, in words the owner can act on."""
    message = str(exc)
    if "credit balance" in message.lower() or "billing" in message.lower():
        return ("the Claude account is out of credit -- add credits at console.anthropic.com "
                "(Plans & Billing), then the agent can carry on")
    if "rate limit" in message.lower():
        return "the Claude account hit its rate limit -- it will work again shortly"
    return message.splitlines()[0][:200]


class ClaudeIntegrationError(RuntimeError):
    pass


# A model's way of saying it does not know, which must never be typed into a form as the answer.
_NON_ANSWER = re.compile(
    r"(?:n/?a|none|null|unknown|not applicable|no answer|-+|"
    r"(?:not|no)\s+(?:provided|specified|mentioned|stated|given|available|listed|found|known|included)"
    r"(?:\s+(?:in|on|by|from)\s+(?:the\s+)?(?:resume|cv|profile|candidate'?s? (?:resume|profile)|facts|information))?|"
    r"(?:the\s+)?(?:resume|profile|candidate)\s+does\s+not\s+(?:say|mention|specify|provide|list|include)\b.*|"
    r"(?:i\s+)?(?:do not|don't|cannot|can't)\s+(?:know|determine|tell|find)\b.*|"
    r"information\s+(?:not|un)\s*available)[.!]?", re.IGNORECASE)


def is_non_answer(text: str) -> bool:
    return bool(_NON_ANSWER.fullmatch((text or "").strip()))


# How much of a job posting goes with each question and page: enough for the requirements, not every benefit.
JOB_POSTING_CHARS = 5_000


class ClaudeClient:
    # What the model behind this client is called and what it raises, by kind. gemini_integration.GeminiClient
    # is this class with Gemini's own in their place, so the prompts, the retry loops and the checks on what
    # comes back below serve both, and neither has to know the other's library.
    PROVIDER = "Claude"
    RATE_LIMITED = anthropic.RateLimitError
    STATUS_ERROR = anthropic.APIStatusError
    CONNECTION_ERROR = anthropic.APIConnectionError
    TIMEOUT_ERROR = anthropic.APITimeoutError

    def __init__(self, config: AppConfig):
        self._config = config
        self._client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    @property
    def _model(self) -> str:
        return self._config.anthropic_model

    def _call(self, *, system: str, user_message: str, max_tokens: int = 2000) -> str:
        last_error: Exception | None = None
        max_retries = getattr(self._config, "claude_max_retries", 3)
        for attempt in range(1, max_retries + 1):
            try:
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user_message}],
                    timeout=self._config.claude_request_timeout,
                )
                return "".join(
                    block.text for block in response.content if block.type == "text"
                )
            except self.RATE_LIMITED as exc:
                wait = 2 ** attempt
                logger.warning("Rate limited by %s API, retrying in %ds", self.PROVIDER, wait)
                time.sleep(wait)
                last_error = exc
            except self.STATUS_ERROR as exc:
                logger.error("%s API error (status=%s): %s", self.PROVIDER, exc.status_code, exc.message)
                last_error = exc
                if exc.status_code and exc.status_code < 500:
                    break
                time.sleep(2 ** attempt)
            except self.CONNECTION_ERROR as exc:
                # Gemini 503 "busy or down" is transient and typically clears in
                # 15-30 s; use a longer wait than the exponential backoff for
                # Claude so we don't give up before Gemini recovers.
                wait = min(30 * attempt, 90)
                logger.warning(
                    "Connection error talking to %s API (attempt %d/%d, retrying in %ds): %s",
                    self.PROVIDER, attempt, max_retries, wait, exc,
                )
                last_error = exc
                time.sleep(wait)

        raise ClaudeIntegrationError(f"{self.PROVIDER} API call failed after retries: {last_error}")

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

    # ------------------------------------------------------------------
    # Job description analysis
    # ------------------------------------------------------------------

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
Employment statuses desired: {", ".join(profile.employment_statuses) if profile.employment_statuses else "Not specified"}
"""
        user_message = (
            f"{profile_summary}\n"
            f"CANDIDATE RESUME (source of truth -- do not invent anything beyond this):\n"
            f"{resume.raw_text}\n\n"
            f"TARGET JOB TITLE: {job.title} at {job.company}\n"
            f"JOB DESCRIPTION:\n{job.raw_text}\n\n"
            "Produce a tailored version of the resume's summary and bullet points."
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
Employment statuses desired: {", ".join(profile.employment_statuses) if profile.employment_statuses else "Not specified"}
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

    def answer_single_question(
        self,
        question: str,
        options: Optional[list[str]] = None,
        resume_text: str = "",
        profile: Optional[UserProfile] = None,
        job_title: str = "",
        company: str = "",
        job_text: str = "",
        box: Optional[dict] = None,
    ) -> str:
        """Answers ONE specific unanswered question on behalf of the candidate.
        Extremely fast, token-efficient (< 300 tokens), and strictly grounded in
        the candidate's resume and profile facts.

        An open question ("Why are you interested in joining us?") is written by
        open_answers in plain English, inside the form's word or character limit;
        `box` is what open_answers.read_box saw on the page.
        """
        if not question:
            return ""
        import safety
        # is_attestation covers signatures too. (This called safety.is_signature_prompt, which does not
        # exist: every call raised AttributeError, the caller logged it at debug level, and no question
        # was ever answered this way.)
        if safety.is_attestation(question) or safety.is_legal_status_question(question):
            return ""
        box = box or {}
        if not options:
            import open_answers
            if open_answers.is_open(question, box.get("multiline", False)):
                return self._open_answer(question, resume_text, profile, job_title, company, job_text, box)

        system = (
            "You answer application questions for a real candidate. "
            "Answer ONLY using truthful facts from their profile and resume. "
            "The JOB POSTING says what the employer is asking about -- use it to understand the question and the "
            "role, never as a fact about the candidate: a skill, tool or years of experience counts only if the "
            "resume or profile shows it. "
            "If options are provided, the answer MUST be an exact string from the options list. "
            "If it is a text/essay question (e.g. 'Why are you interested in this role?'), provide a "
            "concise, professional 1-3 sentence response grounded in their background. "
            "Respond with ONLY JSON: {\"answer\": \"<your concise answer>\"}"
        )

        facts_lines = []
        if profile:
            facts_lines.append(f"Candidate: {profile.full_name}")
            if profile.target_titles:
                facts_lines.append(f"Target Titles: {', '.join(profile.target_titles)}")
            if profile.current_position_title and profile.current_employer:
                facts_lines.append(f"Current Role: {profile.current_position_title} at {profile.current_employer}")
            if profile.years_experience:
                facts_lines.append(f"Experience: {profile.years_experience} years")
            if profile.current_location:
                facts_lines.append(f"Location: {profile.current_location}")
            if profile.work_authorization:
                facts_lines.append(f"Work Auth: {profile.work_authorization}")

        snippet = (resume_text or "").strip()[:2500]
        user_message = (
            f"FACTS:\n{chr(10).join(facts_lines)}\n\n"
            f"RESUME EXCERPT:\n{snippet}\n\n"
            f"TARGET JOB: {job_title or 'Engineer'} at {company or 'Company'}\n\n"
            + (f"JOB POSTING:\n{job_text.strip()[:JOB_POSTING_CHARS]}\n\n" if (job_text or "").strip() else "")
            + f"QUESTION: {question}\n"
        )
        if options:
            user_message += f"\nAVAILABLE OPTIONS (choose exactly one):\n{json.dumps(options, indent=2)}"

        try:
            raw = self._call(system=system, user_message=user_message, max_tokens=600)
            data = self._extract_json(raw)
            ans = str(data.get("answer") or "").strip()
            if is_non_answer(ans):
                # "Not provided in the resume" is the model saying it does not know; typed into Steelcase's
                # Work Phone box (29 September) it would have gone to the employer as the owner's phone number.
                logger.info("AI had no answer for %r (%r) -- left for the owner", question[:50], ans[:60])
                return ""
            if options and ans:
                for opt in options:
                    if opt.strip().lower() == ans.lower():
                        return opt
                return ""
            return ans
        except Exception as exc:
            logger.info("Could not answer single question %r: %s", question[:50], exc)
            return ""

    def _open_answer(self, question: str, resume_text: str, profile: Optional[UserProfile],
                     job_title: str, company: str, job_text: str, box: dict) -> str:
        """An open question, written in the owner's plain words by open_answers."""
        import open_answers
        facts = []
        if profile:
            facts.append(f"Name: {profile.full_name}")
            if profile.current_position_title and profile.current_employer:
                facts.append(f"Current role: {profile.current_position_title} at {profile.current_employer}")
            if profile.years_experience:
                facts.append(f"Years of experience: {profile.years_experience}")
            if profile.target_titles:
                facts.append(f"Roles wanted: {', '.join(profile.target_titles)}")
            for level, subject, school, year in profile.education[:2]:
                facts.append(f"Education: {level} in {subject}, {school} {year}".strip())
        facts.append(f"Applying for: {job_title or 'this role'} at {company or 'this company'}")
        limits = open_answers.limits_from(question, box.get("hint", ""), maxlength=box.get("maxlength"),
                                          minlength=box.get("minlength"))
        try:
            answer, left = open_answers.write(
                question, ask=lambda system, user, tokens: self._call(system=system, user_message=user,
                                                                        max_tokens=tokens),
                facts="\n".join(facts), resume_text=resume_text, job_text=job_text,
                hint=box.get("hint", ""), limits=limits)
        except ClaudeIntegrationError as exc:
            logger.info("Could not write an answer to %r: %s", question[:50], exc)
            return ""
        return answer

    # ------------------------------------------------------------------
    # Reading an application page and planning its answers
    # ------------------------------------------------------------------
    PLAN_PAGE_SYSTEM ="""You fill in job applications for one applicant. You are given the page as an
accessibility snapshot (every control with a [ref=...], its current value, its choices, [checked] and
[selected] states) and FACTS about the applicant. Decide how to answer this page and how to move on.

Respond with ONLY JSON:
{
 "page_kind": "application_form" | "job_description" | "chooser" | "sign_in" | "review" | "confirmation" | "captcha" | "error" | "other",
 "step": "<e.g. '3 of 5' when the page shows a step counter, else ''>",
 "answers": [{"ref": "<ref of the control>", "question": "<the question as the page words it>",
              "action": "fill" | "choose" | "check" | "uncheck" | "upload_resume" | "upload_cover_letter",
              "value": "<text to type, or the choice to pick, exactly as offered when there are choices>",
              "source": "profile.<field name> | resume | owner_earlier_answer | job | consent | document"}],
 "leave_for_owner": [{"question": "...", "reason": "...", "required": true | false}],
 "mismatches": [{"question": "...", "on_page": "...", "facts_say": "...",
                 "correct_value": "<the right answer, exactly as offered, or '' if FACTS don't give it>",
                 "source": "profile.<field name the right answer comes from>"}],
 "next": {"ref": "<ref of the control that moves the application on>", "label": "<its name>",
          "kind": "next_step" | "final_submit" | "open_application" | "sign_in" | "consent" | "none"}
}

Rules -- follow every one:
1. Answer ONLY from FACTS: the profile, the resume text, the owner's earlier answers, the job. FACTS.job.description
   is the posting: it tells you what the employer is asking about, never a fact about the applicant -- a skill,
   tool or number of years counts only if the resume or profile shows it. Never invent,
   never guess. The profile comes first: where it has a field for something (name, address, city, postal code,
   phone, email, work authorization...), use the profile, never an earlier answer -- those can be old.
   owner_earlier_answers are for questions the profile does not cover. If FACTS don't answer a question, put it in leave_for_owner (required = whether the page
   marks it required, e.g. with *).
2. Questions about immigration, visas, sponsorship, work authorization, citizenship, criminal history,
   non-compete or other legal status: answer ONLY when a profile field states it, and give that exact field
   as the source (e.g. "profile.requires_visa_sponsorship"). requires_visa_sponsorship true means the
   applicant DOES need sponsorship now or in the future (an H-1B transfer counts).
3. Signatures and certifications ("I certify", "true and complete", signature, e-signature): only when
   FACTS.profile.sign_attestations is true, which means the applicant has authorized signing on their
   behalf. Then check the checkbox (source "consent"), or type the applicant's full legal name exactly as
   profile.full_name into a signature box (source "profile.full_name"). If sign_attestations is not true,
   put them in leave_for_owner. A plain privacy-notice consent may be checked with source "consent".
4. Skip any control that already shows an answer (a value, a [selected] real option, a [checked] radio).
   If an existing answer contradicts FACTS, don't put it in answers: report it in mismatches with the
   right answer (exactly as one of the offered choices) and the profile field it comes from -- the agent
   corrects it from there. Report it only when a profile field clearly gives the right answer.
5. Never touch password boxes. On a sign-in page, return page_kind "sign_in" with no answers.
6. If a CAPTCHA or "verify you are human" challenge is anywhere on the page, return page_kind "captcha",
   no answers, next kind "none".
7. Files: a resume upload control gets action "upload_resume"; a cover letter upload gets
   "upload_cover_letter". A resume already shown as attached needs nothing.
8. Radio buttons and checkboxes: action "check" with the ref of the button whose label -- its name, or the
   text right after it -- is the answer. For "choose", give the value exactly as one of the offered choices when choices are listed; for a
   dropdown whose choices aren't listed, give the value to search for (e.g. the country's name).
9. Names: use profile.first_name, profile.middle_name and profile.last_name exactly as given -- never split
   the full name yourself. An empty middle name means there is none: leave that box empty.
10. Phone numbers: use the digits as in the profile; where a country code is asked separately, use the
   profile's phone_country_code.
11. next: the button or link that moves the application forward on this page (Next, Continue, Save and
   Continue, Update Profile, Apply, Submit...). kind "final_submit" when pressing it sends the application
   (a last step, a review page, or "Submit Application"); "next_step" when more steps follow;
   "open_application" for Apply on a job posting or an apply-method chooser (prefer applying manually /
   without an account); "consent" for accepting a privacy notice dialog. NEVER choose Finish Later, Save for
   later, Cancel, Back, Withdraw, Log out, or sign-in with LinkedIn/Indeed/Facebook/Apple/Microsoft.
   Where a page offers "Sign in with Google" (or "Continue with Google"), that is always the sign-in to
   choose, ahead of email/password or any other provider (kind "sign_in"). "none" when nothing should be pressed.
12. Sections that need entries added (work history, education, references): press the section's "Add
   Experience" / "Add Education" button as next with kind "next_step", then fill the boxes that appear from
   the resume -- most recent first -- until what the form asks for is met (for example three consecutive
   years of work history). Do not leave such a section to the owner.
13. A question whose choices are a group of radio buttons or Yes/No buttons with no reference of their own:
   give the GROUP's ref with action "check" and the value as the choice's words.
13a. An entry form open on the page (its boxes are filled and it shows Cancel beside its own Add/Save button)
   must be saved before anything else: answer whatever is still empty in it, then give that Add/Save button
   as next with kind "next_step". Where the page shows its own complaint next to a box ("The End Date field
   is required", "(1 issue)"), that box is what is stopping it -- answer that one first.
13b. For the job the owner still holds, tick "Current Job" / "I currently work here" (action "check")
   instead of inventing an end date; a job that has ended gets its real end date from the resume.
14. A confirmation that the application was received: page_kind "confirmation", next kind "none".
"""

    def plan_page(self, snapshot: str, facts: dict[str, Any], feedback: str = "") -> dict[str, Any]:
        """How to answer one application page, from its accessibility snapshot.

        The agent's code carries the plan out and enforces what may never be
        done; this only proposes. See page_agent.py.
        """
        user = (
            "FACTS:\n" + json.dumps(facts, ensure_ascii=False, default=str) +
            "\n\nPAGE SNAPSHOT:\n" + snapshot +
            (f"\n\nWHAT HAPPENED LAST TIME ON THIS PAGE: {feedback}" if feedback else "")
        )
        last_error: Exception | None = None
        cut_off = ""
        for attempt in range(1, self._config.claude_max_retries + 1):
            try:
                response = self._client.messages.create(
                    # Room for a long form: R+L's application page is 43,000
                    # characters and its plan ran past a 6,000-token reply,
                    # which arrived cut off and could not be read at all.
                    model=self._model, max_tokens=16_000, system=self.PLAN_PAGE_SYSTEM,
                    messages=[{"role": "user", "content": user + cut_off}],
                    timeout=max(self._config.claude_request_timeout, 180.0),
                )
                raw = "".join(block.text for block in response.content if block.type == "text")
                start, end = raw.find("{"), raw.rfind("}")
                return self._extract_json(raw[start:end + 1] if start >= 0 and end > start else raw)
            except (self.RATE_LIMITED, self.CONNECTION_ERROR, self.TIMEOUT_ERROR) as exc:
                last_error = exc
                time.sleep(2 ** attempt)
            except ClaudeIntegrationError as exc:
                last_error = exc
                # The reply was cut off mid-JSON: ask for a shorter one.
                cut_off = ("\n\nYour last reply was cut off before the JSON ended. Answer again with JSON only, "
                           "and keep it short: the questions that matter most on this page (at most 20 answers), "
                           "no explanations.")
            except Exception as exc:
                last_error = exc
                break
        raise ClaudeIntegrationError(f"Could not plan the page: {explain(last_error)}")

    # ------------------------------------------------------------------
    # Looking at the page
    # ------------------------------------------------------------------
    def read_page(self, screenshot_png: bytes, page_url: str, goal: str) -> dict[str, Any]:
        """What a person would see on this page, and what they would click next.

        The agent's usual reading works from the page's code, and misses what is
        obvious on screen: a form inside a frame, a Next that is only an arrow,
        a sign-in that is only a logo. This looks at the screenshot instead.

        Returns {"page": kind, "click": visible text or label of the one thing
        to click next ("" for none), "why": short reason}. It never proposes
        submitting an application, signing or certifying anything, typing a
        password, or signing in with LinkedIn, Indeed or Facebook.
        """
        import base64

        system = (
            "You look at a screenshot of a web page during a job application and say what "
            "kind of page it is and the ONE control a person should click next to move the "
            "application forward. Respond with ONLY JSON: "
            '{"page": "job_description" | "application_form" | "sign_in" | "chooser" | '
            '"confirmation" | "error" | "captcha" | "other", '
            '"click": "<the exact visible text, or the label a screen reader would read, of the '
            'control to click; empty string if nothing should be clicked>", '
            '"why": "<one short sentence>"}. '
            "Rules: if a CAPTCHA, picture puzzle, 'verify you are human' check or any challenge "
            "is showing anywhere on the page, return page 'captcha' and an empty click -- never "
            "choose any of its controls (Skip, Verify, refresh, audio, the images); "
            "never choose a control that submits or sends the application (Submit, Send "
            "application, Finish); never choose one that certifies, attests, signs or agrees to "
            "legal terms; never choose sign-in with LinkedIn, Indeed or Facebook; prefer Apply "
            "or Apply Now on a job description, Next or Continue on a form step, and 'Apply "
            "without an account' or 'Sign in with Google' where offered. If the page is an "
            "application form whose fields are simply waiting to be filled, return an empty click."
        )
        user_content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(screenshot_png).decode("ascii")}},
            {"type": "text", "text": f"Page address: {page_url}\nGoal: {goal}"},
        ]
        last_error: Exception | None = None
        for attempt in range(1, self._config.claude_max_retries + 1):
            try:
                response = self._client.messages.create(
                    model=self._model, max_tokens=400, system=system,
                    messages=[{"role": "user", "content": user_content}],
                    timeout=self._config.claude_request_timeout,
                )
                raw = "".join(block.text for block in response.content if block.type == "text")
                result = self._extract_json(raw)
                return {"page": str(result.get("page") or "other"),
                        "click": str(result.get("click") or "").strip(),
                        "why": str(result.get("why") or "").strip()}
            except (self.RATE_LIMITED, self.CONNECTION_ERROR) as exc:
                last_error = exc
                time.sleep(2 ** attempt)
            except Exception as exc:
                last_error = exc
                break
        raise ClaudeIntegrationError(f"Could not read the page: {explain(last_error)}")

    # ------------------------------------------------------------------
    # Cover letter generation
    # ------------------------------------------------------------------
    def generate_cover_letter(
        self, resume: ResumeData, job: JobDescription, profile: UserProfile,
        extra_instruction: str = "",
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
        if extra_instruction:
            user_message += f"\n\nIMPORTANT CORRECTION:\n{extra_instruction}"
        return self._call(system=system, user_message=user_message, max_tokens=1200)
