"""Setting up a person's profile, and keeping their saved answers.

The agent is for anyone who runs it on their own computer (the owner's decision of 29 September 2026). A new
person has no profile, and the agent answers every form from one: so the first thing the dashboard does is set it
up. They upload their resume; the agent drafts a profile from it; they check it, fill in what a resume does not say
(visa, salary, voluntary disclosures, what the agent may do for them) and save. Every application then answers in
this order: the profile, the saved answers, the AI, and only then the person -- and whatever the person answers is
saved and used from then on.

Everything is kept in the person's own data/ folder: data/profile.json and data/profile_answers.json, the same
files the agent reads while applying. Nothing is sent anywhere by setting up, except the resume text to the AI when
an AI key is set, to draft the work history -- the same AI the person already chose for their applications.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional

import config

logger = logging.getLogger(__name__)

ANSWERS_PATH = config.DATA_DIR / "profile_answers.json"
UNANSWERED_PATH = config.DATA_DIR / "unanswered_questions.json"

YES_NO = ("", "Yes", "No")


@dataclasses.dataclass(frozen=True)
class Field:
    name: str                      # the UserProfile field
    label: str
    kind: str = "text"             # text | email | number | bool | choice | list | lines
    choices: tuple[str, ...] = ()
    hint: str = ""
    required: bool = False


# The profile form, section by section. Labels are for a person, not a programmer; every field is a UserProfile
# field, so what is saved here is exactly what the agent answers from.
SECTIONS: tuple[tuple[str, str, tuple[Field, ...]], ...] = (
    ("About you", "How forms should name and reach you.", (
        Field("full_name", "Full legal name", required=True),
        Field("first_name", "First name", hint="Leave blank to take it from your full name."),
        Field("middle_name", "Middle name", hint="Leave blank if you have none."),
        Field("last_name", "Last name", hint="Leave blank to take it from your full name."),
        Field("email", "Email", "email", required=True,
              hint="The address employers write to, and the one the agent uses for job-site accounts."),
        Field("phone_mobile", "Mobile phone", hint="Digits as you would type them, e.g. 5715550100."),
        Field("phone_country_code", "Phone country code", hint="e.g. +1"),
        Field("linkedin_url", "LinkedIn profile link"),
        Field("portfolio_url", "Website or portfolio link"),
    )),
    ("Where you live", "Used for address boxes and 'where are you located' questions.", (
        Field("address_line1", "Street address"),
        Field("city", "City"),
        Field("state", "State or province"),
        Field("county", "County", hint="Some forms ask for it separately."),
        Field("postal_code", "ZIP or postal code"),
        Field("country", "Country"),
        Field("open_to_relocation", "I am open to relocating", "bool"),
        Field("locations", "Places you want to work", "list", hint="Comma separated, e.g. Remote, New York, NY"),
    )),
    ("Work authorization", "Answered only from what you state here -- the agent never guesses these.", (
        Field("country_of_citizenship", "Country of citizenship"),
        Field("us_citizen", "US citizen", "choice", YES_NO),
        Field("legally_eligible_to_work", "Legally allowed to work in the country of the job", "choice", YES_NO),
        Field("authorized_for_any_employer", "Allowed to work for any employer (not tied to one)", "choice", YES_NO),
        Field("requires_visa_sponsorship", "I need visa sponsorship now or in the future", "bool",
              hint="Includes an H-1B transfer. Jobs that say they will not sponsor are then skipped."),
        Field("work_authorization", "Your status in your own words", hint="e.g. H-1B, Green card, US citizen"),
        Field("security_clearance", "Security clearance", "choice", YES_NO),
        Field("security_clearance_level", "Clearance level", hint="Leave blank if none."),
        Field("at_least_18", "18 or older", "choice", YES_NO),
    )),
    ("Your work", "Most of this comes from your resume; check it.", (
        Field("current_position_title", "Current or last job title"),
        Field("current_employer", "Current or last employer"),
        Field("current_employer_location", "Where that job is"),
        Field("current_employment_dates", "Dates at that job", hint="e.g. 02/2025 - Present"),
        Field("years_experience", "Years of experience", "number"),
        Field("people_managed", "People you manage", hint="A number, or leave blank."),
        Field("target_titles", "Job titles you are applying for", "list", hint="Comma separated"),
        Field("education", "Education", "lines",
              hint="One per line: degree level | field | school | year. e.g. Master's | Computer Science | "
                   "State University | 2022"),
    )),
    ("Job preferences", "What forms ask about pay, timing and how you work.", (
        Field("salary_min", "Lowest salary you would accept (per year)", "number"),
        Field("salary_max", "Top of your salary range (per year)", "number"),
        Field("bonus_expectations", "Bonus expectations", hint="Leave blank to be asked."),
        Field("availability_to_start", "When you can start", hint="e.g. 2 weeks' notice"),
        Field("willing_to_travel", "Willing to travel", "choice", YES_NO),
        Field("willing_to_work_onsite_three_days", "Willing to work in the office 3 days a week", "choice", YES_NO),
        Field("willing_to_work_weekends", "Willing to work weekends", "choice", YES_NO),
        Field("work_arrangements", "Ways of working you accept", "list", hint="e.g. Remote, Hybrid, On-site"),
        Field("how_did_you_hear", "Usual answer to 'How did you hear about us?'", hint="e.g. LinkedIn"),
        Field("reason_for_leaving", "Why you are leaving (or left) your current job"),
    )),
    ("Standard screening questions", "Questions most employers ask. Leave any blank to be asked each time.", (
        Field("felony_conviction", "Ever convicted of a felony", "choice", YES_NO),
        Field("previously_employed_here", "Default answer to 'Have you worked for us before?'", "choice", YES_NO),
        Field("applied_here_before", "Default answer to 'Have you applied to us before?'", "choice", YES_NO),
        Field("relatives_employed_here", "Default answer to 'Do relatives work here?'", "choice", YES_NO),
        Field("bound_by_non_compete", "Bound by a non-compete agreement", "choice", YES_NO),
        Field("willing_drug_test_and_physical", "Willing to take a drug test / physical", "choice", YES_NO),
        Field("willing_to_submit_to_pre_employment_background_check", "Willing to take a background check",
              "choice", YES_NO),
        Field("outside_business_interests_with_competitors", "Business interests with competitors", "choice",
              YES_NO),
        Field("preferred_language", "Preferred language"),
    )),
    ("Voluntary disclosures", "Optional. Employers must not use these to decide. 'I don't wish to answer' is "
                              "always accepted.", (
        Field("gender", "Gender"),
        Field("hispanic_or_latino", "Hispanic or Latino", "choice", YES_NO + ("I don't wish to answer",)),
        Field("ethnicity", "Race / ethnicity"),
        Field("veteran_status", "Veteran status", hint="e.g. I am not a protected veteran"),
        Field("disability_status", "Disability status", hint="e.g. No, I don't have a disability"),
    )),
    ("What the agent may do for you", "Off unless you turn it on.", (
        Field("accept_application_privacy_prompts", "Accept privacy notices that block a form", "bool"),
        Field("sign_attestations", "Sign 'I certify this is true' declarations for me", "bool",
              hint="Only once every other answer on the page came from your profile."),
        Field("check_gmail_for_confirmation", "Read my Gmail for confirmation emails and one-time codes", "bool",
              hint="Only in the agent's own browser, signed in to your application email."),
    )),
)

FIELDS = {f.name: f for _title, _about, fields in SECTIONS for f in fields}


# ---------------------------------------------------------------------------
# Has this person set up?
# ---------------------------------------------------------------------------
def needs_setup() -> bool:
    """No profile yet, or still the template's placeholders."""
    if not config.PROFILE_PATH.is_file():
        return True
    try:
        profile = config.get_user_profile()
    except RuntimeError:
        return True
    default = config.UserProfile()
    return not profile.email or profile.email == default.email or profile.full_name == default.full_name


def current_values() -> dict[str, Any]:
    """The saved profile as plain values, for the form."""
    try:
        profile = config.get_user_profile()
    except RuntimeError:
        profile = config.UserProfile()
    return {name: getattr(profile, name) for name in FIELDS}


# ---------------------------------------------------------------------------
# A draft from the resume
# ---------------------------------------------------------------------------
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
_LINKEDIN = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w-]+/?", re.IGNORECASE)
_CITY_STATE = re.compile(r"\b([A-Z][a-zA-Z.' -]{1,30}),\s*([A-Z]{2})\b")
_YEARS = re.compile(r"\b(\d{1,2})\+?\s*(?:years|yrs)\b", re.IGNORECASE)


def draft_from_text(text: str) -> dict[str, Any]:
    """What a resume says plainly, read without any AI: name, email, phone, LinkedIn, city and state, years.

    Only what is there is taken; nothing is filled in by guessing."""
    draft: dict[str, Any] = {}
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for line in lines[:4]:
        words = line.split()
        if 2 <= len(words) <= 5 and all(re.fullmatch(r"[A-Za-z][A-Za-z.'-]*", w) for w in words) \
                and not re.search(r"engineer|manager|developer|analyst|resume|curriculum", line, re.IGNORECASE):
            draft["full_name"] = " ".join(w.capitalize() if w.isupper() else w for w in words)
            # Shown split, for the person to correct: whether 'Marie' in 'Jane Marie Doe' is a middle name
            # or part of the first name is theirs to say, not the agent's.
            draft["first_name"], draft["middle_name"], draft["last_name"] = split_name(draft["full_name"])
            break
    head = "\n".join(lines[:8])
    if m := _EMAIL.search(text or ""):
        draft["email"] = m.group(0)
    if m := _PHONE.search(head):
        draft["phone_mobile"] = re.sub(r"\D", "", m.group(0))[-10:]
    if m := _LINKEDIN.search(text or ""):
        url = m.group(0)
        draft["linkedin_url"] = url if url.lower().startswith("http") else "https://" + url
    if m := _CITY_STATE.search(head):
        draft["city"], draft["state"] = m.group(1).strip(), m.group(2)
    years = [int(y) for y in _YEARS.findall(text or "") if 0 < int(y) < 60]
    if years:
        draft["years_experience"] = max(years)
    return draft


_AI_FIELDS = ("full_name", "email", "phone_mobile", "linkedin_url", "city", "state", "country",
              "current_position_title", "current_employer", "current_employer_location",
              "current_employment_dates", "years_experience", "target_titles", "education")

_AI_SYSTEM = f"""You read a resume and copy facts out of it into JSON. Copy only what the resume says; never infer,
guess or add. Leave out any key the resume does not state.

Keys: {", ".join(_AI_FIELDS)}.
- target_titles: a list with the resume's own headline title (at most 3).
- education: a list of [degree level, field of study, school, year finished], newest first.
- current_employment_dates: as written, e.g. "02/2025 - Present".
- years_experience: a whole number, only if the resume states it.

Respond with ONLY the JSON object."""


def draft_from_resume(text: str, ask: Optional[Callable[[str, str, int], str]] = None) -> dict[str, Any]:
    """A draft profile from the resume's text: what it plainly says, and -- with an AI -- the work history.

    `ask(system, user, max_tokens)` calls the AI the person chose; without one, or if it fails, the plain read
    is all there is and the person fills in the rest. What the AI returns is kept only for known fields."""
    draft = draft_from_text(text)
    if ask is None:
        return draft
    try:
        raw = ask(_AI_SYSTEM, "RESUME:\n" + (text or "")[:12_000], 1_500)
        match = re.search(r"\{.*\}", raw or "", re.DOTALL)
        found = json.loads(match.group(0)) if match else {}
    except Exception as exc:
        logger.info("Could not draft the profile with the AI: %s", str(exc).splitlines()[0][:120] if str(exc) else exc)
        return draft
    for key in _AI_FIELDS:
        value = found.get(key) if isinstance(found, dict) else None
        if value in (None, "", [], {}):
            continue
        if key == "education":
            rows = [tuple(str(part or "").strip() for part in row)[:4] for row in value
                    if isinstance(row, (list, tuple)) and len(row) >= 3]
            if rows:
                draft[key] = tuple(r + ("",) * (4 - len(r)) for r in rows)
        elif key == "target_titles":
            titles = [str(t).strip() for t in (value if isinstance(value, list) else [value]) if str(t).strip()]
            if titles:
                draft[key] = tuple(titles[:3])
        elif key == "years_experience":
            try:
                draft[key] = int(value)
            except (TypeError, ValueError):
                pass
        else:
            draft.setdefault(key, str(value).strip())        # what the resume plainly says comes first
    return draft


# ---------------------------------------------------------------------------
# Reading the form and saving
# ---------------------------------------------------------------------------
def values_from_form(form: dict[str, Any]) -> dict[str, Any]:
    """The profile values a submitted form holds, each in the type its field takes."""
    values: dict[str, Any] = {}
    for name, f in FIELDS.items():
        raw = form.get(name)
        if f.kind == "bool":
            values[name] = raw in ("on", "true", "1", True)
            continue
        text = str(raw or "").strip()
        if f.kind == "number":
            values[name] = int(re.sub(r"[^\d]", "", text) or 0)
        elif f.kind == "list":
            values[name] = tuple(part.strip() for part in text.split(",") if part.strip())
        elif f.kind == "lines":
            rows = []
            for line in text.splitlines():
                parts = [p.strip() for p in line.split("|")]
                if any(parts):
                    rows.append(tuple((parts + ["", "", "", ""])[:4]))
            values[name] = tuple(rows)
        elif f.kind == "choice" and text not in f.choices:
            values[name] = ""
        else:
            values[name] = text
    return values


def problems(values: dict[str, Any]) -> list[str]:
    """What must be put right before the profile can be saved."""
    found = []
    for name, f in FIELDS.items():
        if f.required and not str(values.get(name) or "").strip():
            found.append(f"{f.label} is needed.")
    email = str(values.get("email") or "")
    if email and not _EMAIL.fullmatch(email):
        found.append("The email address does not look right.")
    if values.get("salary_min") and values.get("salary_max") and values["salary_min"] > values["salary_max"]:
        found.append("The lowest salary is above the top of the range.")
    return found


def split_name(full_name: str) -> tuple[str, str, str]:
    """First, middle, last from a full name: the first word, the last word, what is between."""
    words = full_name.split()
    if not words:
        return "", "", ""
    if len(words) == 1:
        return words[0], "", ""
    return words[0], " ".join(words[1:-1]), words[-1]


def save_profile(values: dict[str, Any], path: Optional[Path] = None) -> Path:
    """Saves the profile to data/profile.json, keeping any field the form does not show.

    Blank first/last names are taken from the full name. Raises ValueError with what is wrong."""
    found = problems(values)
    if found:
        raise ValueError(" ".join(found))
    path = Path(path or config.PROFILE_PATH)
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            existing = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            existing = {}
    merged = {**existing, **{k: (list(map(list, v)) if k == "education" else list(v) if isinstance(v, tuple) else v)
                             for k, v in values.items()}}
    first, middle, last = split_name(str(merged.get("full_name") or ""))
    if not merged.get("first_name"):
        merged["first_name"] = first
    if not merged.get("last_name"):
        merged["last_name"] = last
    if "middle_name" not in values and not merged.get("middle_name"):
        merged["middle_name"] = middle
    merged.setdefault("phone", merged.get("phone_mobile", ""))
    if merged.get("city") and merged.get("state") and not merged.get("current_location"):
        merged["current_location"] = f"{merged['city']}, {merged['state']}"
    allowed = set(config.UserProfile.__dataclass_fields__)
    merged = {k: v for k, v in merged.items() if k in allowed}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


# ---------------------------------------------------------------------------
# Saved answers
# ---------------------------------------------------------------------------
def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def readable_question(key: str) -> str:
    """A saved answer's key as a person reads it: a matching pattern shown without its regex marks."""
    if not key.startswith("re:"):
        return key
    text = key[3:]
    text = re.sub(r"\\s[*+]|\\s|\(\?:|\)|\^|\$|\\b", " ", text)
    text = re.sub(r"\\(.)", r"\1", text).replace(".*", " ... ")
    return re.sub(r"\s+", " ", text).strip()


def saved_answers(path: Optional[Path] = None) -> list[dict[str, str]]:
    """Every saved answer: its key, the question as a person reads it, and the answer."""
    rows = []
    for key, value in _read(Path(path or ANSWERS_PATH)).items():
        if not isinstance(key, str):
            continue
        shown = value.get("value", "") if isinstance(value, dict) else value
        if isinstance(shown, bool):
            shown = "Yes" if shown else "No"
        rows.append({"key": key, "question": readable_question(key), "answer": str(shown)})
    return sorted(rows, key=lambda r: r["question"].lower())


def waiting_questions(unanswered: Optional[Path] = None, answers: Optional[Path] = None) -> list[dict[str, Any]]:
    """Questions the agent had to leave, that have no saved answer yet: the most asked first."""
    saved = _read(Path(answers or ANSWERS_PATH))
    rows = []
    for entry in _read(Path(unanswered or UNANSWERED_PATH)).values():
        if not isinstance(entry, dict) or not entry.get("answer_key") or entry.get("answer_key") in saved:
            continue
        rows.append({"key": entry["answer_key"], "question": entry.get("question", ""),
                     "options": entry.get("options") or [], "times": entry.get("times", 1),
                     "companies": entry.get("companies") or []})
    return sorted(rows, key=lambda r: -int(r.get("times") or 1))


def save_answers(changes: dict[str, str], removed: tuple[str, ...] = (), path: Optional[Path] = None) -> Path:
    """Adds or changes saved answers (key -> answer) and removes the keys in `removed`.

    A blank answer is not saved: blank means 'ask me'. A new question typed as plain text is saved as that text,
    which the agent matches however a form punctuates or cuts it short."""
    path = Path(path or ANSWERS_PATH)
    answers = _read(path)
    for key in removed:
        answers.pop(key, None)
    for key, answer in changes.items():
        key, answer = (key or "").strip(), (answer or "").strip()
        if not key:
            continue
        if answer:
            answers[key] = answer
        else:
            answers.pop(key, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(answers, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


# ---------------------------------------------------------------------------
# Learning from what the person answers while applying
# ---------------------------------------------------------------------------
_ABOUT_THIS_EMPLOYER = re.compile(
    r"\b(this|our) (company|organi[sz]ation|firm|team|employer|position|role|job|opening)\b|\bfor us\b|\bwith us\b|"
    r"\bhere\b|\bjoin(ing)? us\b|refer(red|ral|rer)?\b|employee (id|number)|requisition|job (id|code|number)|"
    r"\bwhy (do|are|would) you\b", re.IGNORECASE)
KEEP_AT_MOST = 200          # longer is an essay written for one job, not an answer to reuse


def worth_keeping(question: str, answer: str, company: str = "") -> bool:
    """Whether an answer the person gave on one form is theirs for every form: a general question, answered in a
    few words. Anything about the employer they gave it to, an essay, or a legal or signed statement is not.

    The one rule for what joins the saved answers from an application (apply_flow.learn_user_answers asks it)."""
    import safety
    question, answer = " ".join((question or "").split()), (answer or "").strip()
    if len(question) < 6 or not answer or len(answer) > KEEP_AT_MOST:
        return False
    if safety.is_attestation(question) or safety.is_legal_status_question(question) or safety.is_attestation(answer):
        return False
    import concept_matcher
    if concept_matcher.match_concept(question) in ("WORK_AUTHORIZATION", "VISA_SPONSORSHIP", "CITIZENSHIP"):
        return False            # answered from the profile only, every time -- never from a saved answer
    if _ABOUT_THIS_EMPLOYER.search(question):
        return False
    words = [w for w in re.findall(r"[A-Za-z]{4,}", company or "")
             if w.lower() not in {"inc", "corp", "corporation", "company", "group", "services", "llc", "limited"}]
    if any(re.search(rf"\b{re.escape(w)}\b", question, re.IGNORECASE) for w in words):
        return False
    return True


def remember_answer(question: str, answer: str, company: str = "", path: Optional[Path] = None) -> bool:
    """Adds an answer the person gave while applying to their saved answers, when it is worth keeping and no saved
    answer already covers the question. Returns True when it was added. A saved answer is never overwritten here:
    what the person set on the dashboard stays theirs."""
    if not worth_keeping(question, answer, company):
        return False
    path = Path(path or ANSWERS_PATH)
    question = " ".join(question.split()).rstrip(" *")
    saved = _read(path)
    lowered = question.lower()
    for key in saved:
        if key.lower() == lowered:
            return False
        if key.startswith("re:"):
            try:
                if re.search(key[3:], question, re.IGNORECASE):
                    return False
            except re.error:
                continue
    save_answers({question: answer}, path=path)
    return True
