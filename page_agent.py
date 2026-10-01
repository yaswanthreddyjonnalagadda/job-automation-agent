"""
The agent that reads each page and works it out -- instead of code written for
each site.

Every page is read the way a screen reader reads it: each question with its
current answer and its choices, every button, the step counter, any error --
from Playwright's accessibility snapshot, which covers pages inside frames too.
Claude decides what each question should be answered with, from the profile,
the resume and the owner's own earlier answers. This module does it through
the snapshot's references and reads the page again to confirm every answer
stuck.

What it may never do is decided here, in code, whatever Claude proposes:
  * never submit while a sponsorship or work-authorization answer on the page
    contradicts the profile, a CAPTCHA shows, a declaration is waiting, or a
    required question is left for the owner
  * sign a declaration or signature only when the owner allows it
    (profile.sign_attestations) and only after everything else on the page
    came from the profile, contradicts nothing and leaves nothing required blank
  * never type a password (the sign-in code handles employer accounts)
  * never change an answer the owner gave; an answer the site pre-filled that
    contradicts the profile is corrected from the profile (the owner's rule,
    2026-09-17: the agent does the work, not the owner)
  * never answer an immigration, criminal or contract question except from a
    profile field that states it
  * never press Finish Later, Withdraw, Log out, Cancel, Back or a social sign-in
  * record "submitted" only when the site itself confirms it
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import concept_matcher
import emailed_codes
import geo_reference
import account_state
import field_requirements
import evidence
import job_sources
import answer_bank
from claude_integration import JOB_POSTING_CHARS, is_non_answer
import employment_history
import form_fields
import location_choice
import option_match
import repeated_entries
import login_guard
import open_answers
from config import BLANK_MEANS_NONE, resume_to_attach, site_prefill_policy
from interaction import (
    click_resiliently,
    commit_draft_cards,
    fill_and_dispatch,
    resolve_ant_dropdown,
    sweep_modals_and_policies,
    wipe_and_enforce_location_sweep,
)
from perception import (
    hide_secrets,
    is_ant_dropdown,
    is_ant_single_select,
    is_honeypot,
    is_secret_box,
)
import provenance
import safety
from page_reading import (  # noqa: F401 -- the reading stage, re-exported for existing callers
    ANSWER_ROLES,
    Control,
    FORWARD_LABEL,
    NEVER_PRESS,
    OPTION_ROLES,
    PLACEHOLDER,
    PRESS_ROLES,
    _ACTION_WORDS,
    _BOT_TRAP,
    _CHOICE_WORDS,
    _ERROR_TEXT,
    _INTRODUCES_CHOICES,
    _LINE,
    _NOT_AN_ERROR,
    _REQUIRED_NOTE,
    _SELECTED_TAG,
    _STATEMENT,
    _STEP,
    _TAG_CHOICE,
    _TAG_LIST,
    _UPLOAD_VERB,
    _blank_choice_on_page,
    _group_tick_boxes,
    _last_entry_box,
    _plain,
    _same_question,
    _section_holds_a_file,
    _settle_toggle_row,
    _unquote,
    _upload_control,
    current_step,
    frames_loading,
    host_of,
    is_bot_trap,
    is_workday_choice_button,
    page_errors,
    parse_snapshot,
    required_questions,
    still_loading,
)

logger = logging.getLogger("page_agent")

MAX_PAGES = 30          # steps in one run before handing over
MAX_TRIES_PER_PAGE = 3  # reads of the same page that failed to move it on
MAX_READS_PER_PAGE = 15  # reads of one page's questions -- enough to add several entries to it


# An upload menu's item for a file on this computer -- never Dropbox, Google Drive, OneDrive or a LinkedIn profile.
_FILE_ON_THIS_COMPUTER = re.compile(r"^\s*(?:file|upload(?:\s+a)?\s+file|upload\s+from\s+(?:this\s+)?(?:device|computer)|"
                                    r"from\s+(?:this\s+|my\s+)?(?:device|computer)|my\s+(?:device|computer)|"
                                    r"local\s+file|browse(?:\s+files?)?|attach\s+file)\s*$", re.IGNORECASE)

# A box that asks for a web address (its label is the site or the word itself, not a question about it), and what a
# web address looks like.
_WEB_ADDRESS_BOX = re.compile(r"^\W*(?:your\s+)?(?:linked\s?in|facebook|twitter|x\s*\(twitter\)|git\s?hub|instagram|"
                              r"portfolio|personal\s+website|website|web\s*site|blog|url)(?:\s+(?:url|link|profile|"
                              r"profile\s+url|address|page))?\W*$", re.IGNORECASE)
_WEB_ADDRESS = re.compile(r"(?:https?://|www\.)\S+|[\w-]+(?:\.[\w-]+)+(?:/\S*)?", re.IGNORECASE)


# Workday's "Autofill with Resume" opens the computer's own file dialog. That
# dialog belongs to Windows, not to the page: nothing on the page can close it,
# and a whole run sat frozen behind it until it was killed.
OPENS_A_FILE_DIALOG = re.compile(
    r"autofill|upload|attach|choose (a )?file|\bbrowse\b|import (your )?(profile|resume|cv)", re.IGNORECASE)

# Workday answered a page press with "Something went wrong. Please refresh the
# page and then try again." -- a passing fault, and the page says what to do.
ASKS_FOR_A_REFRESH = re.compile(r"something went wrong|please (refresh|reload) (the |this )?page|"
                                r"try (again|reloading)", re.IGNORECASE)

# A confirmation says so in words only a confirmation uses. "Thank you for your interest" is
# not one of them: a create-account page opens with it ("Thank you for your interest in a career
# with HonorHealth. Please create an account ...") and was recorded as a submitted application.
# Whether a page says the application has arrived is decided in confirmation.py, for every caller.
from confirmation import CONFIRMATION_TEXT, RECEIVED_TEXT, STILL_TO_DO_TEXT  # noqa: E402

# What a site says when the account behind a sign-in cannot be used.
ACCOUNT_ERROR = re.compile(
    r"account is inactive|account (has been |is )?(inactive|disabled|locked|deactivated|suspended)|"
    r"no account (was )?found|user not found|we (could not|couldn't) find (an )?account|"
    r"sign[- ]?in (failed|unsuccessful)|unable to sign you in",
    re.IGNORECASE)


# Every required question the agent had to leave for the owner, once each, with why and where.
# Plain JSON like the answer library beside it (data/profile_answers.json), so both can be read,
# edited and carried to the next project. The owner's answer goes in the library, under the
# entry's "answer_key".
UNANSWERED_FILE = Path("data/unanswered_questions.json")


# A page or step that is about the resume, in the words sites use for it.
_RESUME_PAGE = re.compile(r"upload a file|resume upload|\b(select|choose|upload|attach|add|submit)\s+(your\s+|a\s+)?"
                          r"(resume|cv|curriculum vitae)\b", re.IGNORECASE)
# A button that opens the computer's file picker.
_FILE_BUTTON = re.compile(r"\b(select|choose|pick)\s+(another\s+|a\s+|your\s+)?files?\b|\battach\b|\bupload\b|\bbrowse\b",
                          re.IGNORECASE)
_RESUME_WORDS = re.compile(r"resume|\bcv\b", re.IGNORECASE)
# A resume/CV label marked required, as the snapshot shows it: seen even when the
# upload beside it is not one the agent recognises.
_REQUIRED_RESUME_LABEL = re.compile(
    r"^\s*-\s*(?:generic|text|heading|legend|label|paragraph)\b[^:\n]*:\s*"
    r"(?:resume|cv|curriculum vitae)\b[^*\n]{0,30}\*\s*$", re.IGNORECASE | re.MULTILINE)


# The owner's own details, and the profile field each box takes. An old
# address remembered from another employer's pre-filled form was being written
# into new applications; the profile is what the owner keeps up to date.
_DETAIL_FIELDS = (
    (re.compile(r"^\W*(street |home |mailing )?address(\s*(line\s*)?1)?\W*$", re.IGNORECASE), "address_line1"),
    (re.compile(r"^\W*(city|town)\b", re.IGNORECASE), "city"),
    (re.compile(r"^\W*(state|province|state\s*\/\s*province|region)\b", re.IGNORECASE), "state"),
    (re.compile(r"^\W*(country|country\s*\/\s*region|residence\s*country)\b", re.IGNORECASE), "country"),
    (re.compile(r"^\W*(zip|postal)\s*(code)?\b", re.IGNORECASE), "postal_code"),
    (re.compile(r"^\W*e-?mail\b", re.IGNORECASE), "email"),
)

# The name boxes a form asks for, and the profile field each one takes.
_NAME_FIELDS = (
    (re.compile(r"^\W*(legal\s+|given\s+)?first\s*(name)?\b", re.IGNORECASE), "first_name"),
    (re.compile(r"^\W*(legal\s+)?(last|family|sur)\s*name\b", re.IGNORECASE), "last_name"),
    (re.compile(r"^\W*(legal\s+)?middle\s*(name|initial)?\b", re.IGNORECASE), "middle_name"),
    (re.compile(r"^\W*(full|legal)\s*(legal\s*)?name\b", re.IGNORECASE), "full_name"),
)


def _detail_field(question: str) -> str:
    """Which of the owner's own details a box is asking for, or ""."""
    question = " ".join((question or "").split())
    if not question or re.search(r"employer|company|school|university|reference|referr|emergency|previous|"
                                 r"supervisor|manager|work address", question, re.IGNORECASE):
        return ""
    # A place of birth, origin, issuance or a visa is not where the owner lives ("Country of Birth").
    if re.search(concept_matcher.NOT_WHERE_YOU_LIVE, question, re.IGNORECASE):
        return ""
    # "Country Phone Code" asks for a dial code, not where the owner lives: read as the country, Rackspace's
    # "United States of America (+1)" was "corrected" to "United States" and the run stopped (29 September).
    if re.search(r"phone|dial|calling|mobile|telephone|\bcode\b", question, re.IGNORECASE) \
            and not re.search(r"\b(zip|postal)\b", question, re.IGNORECASE):
        return ""
    for pattern, field in _DETAIL_FIELDS:
        if pattern.search(question):
            return field
    return ""


# Answers the owner has settled once and for all, and the profile field that
# holds each. A form asks these in its own words on every site.
_STANDING_ANSWERS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"sponsor", re.IGNORECASE), "requires_visa_sponsorship"),
    (re.compile(r"legally (eligible|authori[sz]ed)|authori[sz]ed to work|right to work", re.IGNORECASE),
     "legally_eligible_to_work"),
    (re.compile(r"(citizen(ship)?|nationality|country of citizenship)", re.IGNORECASE),
     "country_of_citizenship"),
    (re.compile(r"protected veteran|veteran status", re.IGNORECASE), "veteran_status"),
    (re.compile(r"disability status|self.identif(y|ication).{0,30}disab", re.IGNORECASE),
     "disability_status"),
    (re.compile(r"hispanic|latino", re.IGNORECASE), "hispanic_or_latino"),
    (re.compile(r"(?<!regardless of )\b(ethnic|race)\b", re.IGNORECASE), "ethnicity"),
    (re.compile(r"\bgender\b|sex at birth", re.IGNORECASE), "gender"),
    (re.compile(r"at least 18|over 18|eighteen years", re.IGNORECASE), "at_least_18"),
    (re.compile(r"felony|criminal conviction|convicted of a crime", re.IGNORECASE), "felony_conviction"),
    (re.compile(r"relocat", re.IGNORECASE), "open_to_relocation"),
    (re.compile(r"willing to travel|travel (for|up to)", re.IGNORECASE), "willing_to_travel"),
    (re.compile(r"available (to start|start date)|when can you start|notice period", re.IGNORECASE),
     "availability_to_start"),
    (re.compile(r"salary (expectation|range|requirement)|desired (pay|salary)|compensation expectation", re.IGNORECASE),
     "salary_min"),
    (re.compile(r"how did you hear|source of (this )?(job|application)", re.IGNORECASE),
     "how_did_you_hear"),
    (re.compile(r"(ever )?(applied|been interviewed|interviewed) (at|with|for|here|in the past)|previously applied",
                re.IGNORECASE), "applied_here_before"),
    (re.compile(r"(ever )?(been )?employed (at|with|by|here)|previously worked (at|with|by|here)|former employee",
                re.IGNORECASE), "previously_employed_here"),
    (re.compile(r"willing to work weekends?|work weekends?|weekend work|weekend availability",
                re.IGNORECASE), "willing_to_work_weekends"),
    (re.compile(r"(relative|family member).{0,40}(employ|work)", re.IGNORECASE), "relatives_employed_here"),
    (re.compile(r"3 days\s*/?\s*week|3 days per week|onsite 3 days|in person.{0,30}3 days", re.IGNORECASE),
     "willing_to_work_onsite_three_days"),
    (re.compile(r"drug (screen|test)|physical exam|pre-?employment screening", re.IGNORECASE),
     "willing_drug_test_and_physical"),
)


_ADD_ENTRY = re.compile(r"^\s*\+?\s*add\s+(?:another\s+|a\s+|new\s+|more\s+)?(education|degree|school|experience|"
                        r"work(?:\s+experience)?|employment|job|position)\b", re.IGNORECASE)
_TEXT_MESSAGES = re.compile(r"\bsms\b|text\s+messag|\btexts?\b.{0,40}\b(?:phone|mobile|cell)|mobile\s+messag|"
                            r"whatsapp|\btexting\b", re.IGNORECASE)
_AGREEMENT = re.compile(r"\bi\s+(?:have\s+read|acknowledge|agree|understand|accept|consent|certify|confirm|attest)\b|"
                        r"\bby\s+(?:checking|ticking|selecting)\s+this\s+box\b|\bterms\s+(?:and|&)\s+conditions\b|"
                        r"\bprivacy\s+(?:policy|notice|statement)\b|\backnowledg", re.IGNORECASE)
_TICK_WORDS = frozenset({"yes", "no", "true", "false", "checked", "unchecked", "1", "0", "y", "n"})
# The owner's identity and contact facts: the profile says them, over any saved answer.
_PROFILE_OWNS = frozenset({"FIRST_NAME", "LAST_NAME", "MIDDLE_NAME", "FULL_NAME", "EMAIL", "PHONE_MOBILE",
                           "STREET_ADDRESS", "CITY", "STATE_PROVINCE", "POSTAL_CODE", "COUNTRY"})
_REQUIRED_STAR = re.compile(r"^\s*[*✱]|[*✱]\s*:?\s*$")


def _name_field(question: str) -> str:
    """Which name a box is asking for, or "" when it isn't asking for one."""
    question = " ".join((question or "").split())
    if not question or re.search(r"preferred|nickname|maiden|father|mother|spouse|referr|employer|company|school",
                                 question, re.IGNORECASE):
        return ""
    for pattern, field in _NAME_FIELDS:
        if pattern.search(question):
            return field
    return ""


def _recipe_question_key(question: str) -> str:
    """An opaque, punctuation-insensitive key for a learned control."""
    return hashlib.sha256(_plain(question).encode("utf-8")).hexdigest()


def _recipe_options_signature(options: list[str]) -> str:
    """Identifies the exact option list a learned action was proven against."""
    normalised = [" ".join(str(option).split()).casefold() for option in options if str(option).strip()]
    encoded = json.dumps(normalised, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _learnable_recipe_question(question: str) -> bool:
    """Whether mechanics for this question are safe to retain and replay."""
    if not question or safety.is_attestation(question) or safety.is_legal_status_question(question):
        return False
    return not bool(re.search(
        r"\b(?:sponsor|authori[sz]ed to work|work authori[sz]ation|visa|immigration|"
        r"export control|criminal|convict|felony|non-?compete|restrictive covenant)\b",
        question,
        re.IGNORECASE,
    ))


_UPLOAD_ERROR = re.compile(r"upload|attach|resume|\bcv\b|file", re.IGNORECASE)


RUNS_KEPT = 10     # the page recordings of the most recent runs of one application


def keep_latest_runs(folder: Path, keep: int = RUNS_KEPT) -> None:
    """Deletes all but the `keep` most recent run folders (named by their start time) under `folder`."""
    runs = sorted((p for p in Path(folder).iterdir() if p.is_dir() and re.fullmatch(r"\d{8}_\d{6}", p.name)),
                  key=lambda p: p.name)
    import shutil
    for old in runs[:-keep] if keep > 0 else runs:
        shutil.rmtree(old, ignore_errors=True)


def published_choices(question: str, published: list) -> list[str]:
    """The choices the job site publishes for this question (Greenhouse's question list), or [].

    A list that draws its choices only when clicked showed the agent nothing to match an answer to (f007, f021);
    the published list gives the exact wording it takes, before it is opened."""
    for entry in published or ():
        if isinstance(entry, dict) and entry.get("options") and _same_question(str(entry.get("question") or ""),
                                                                                question or ""):
            return [str(o) for o in entry["options"]]
    return []


def _open_is_required(question: str, controls: list["Control"], required: set[str]) -> bool:
    """Whether a question still open is marked required -- by a star, or by the page's own required list."""
    if question.rstrip().endswith("*") or any(_same_question(question, marked) for marked in required):
        return True
    control = next((c for c in controls if _same_question(c.question, question.rstrip("*").strip())), None)
    return control is not None and ("*" in control.question or "*" in (control.name or ""))


# What a form writes instead of the state's name ("va" for "virginia").
_STATE_CODES = geo_reference.us_state_map()


# Where the owner lives, in the order a form's lists depend on each other.
_RESIDENCE_ORDER = {"COUNTRY": 0, "STATE_PROVINCE": 1, "CITY": 2}
# A work or education entry: its places belong to the entry, not to the owner.
_ENTRY_CONTEXT = re.compile(r"experience|employment|employer|work history|education|school|"
                            r"university|college|degree|reference", re.IGNORECASE)


_DIAL_CODE = re.compile(r"\+\s?\d{1,4}")


def _holds_dial_code(control: "Control") -> bool:
    """A phone's country-code picker ("+1" beside the number) is labelled "Country"
    and shows a dial code. That answers which prefix the number takes, never where
    the owner lives: "correcting" it to a country name retyped it on every pass."""
    return bool(_DIAL_CODE.fullmatch((control.selected_option or control.value or "").strip()))


def _same_answer(a: str, b: str) -> bool:
    # "Springfield, IL" is "Springfield, Illinois, United States": one town, two spellings.
    if geo_reference.same_locality(a, b):
        return True
    a, b = _plain(a), _plain(b)
    if not a or not b:
        return False
    # "VA" is "Virginia": the form had it right and the agent reported it as wrong.
    if _STATE_CODES.get(a) == b or _STATE_CODES.get(b) == a:
        return True
    if a == b or a.startswith(b + " ") or b.startswith(a + " "):
        return True
    # Negative veteran status variants (e.g. "I am not a protected veteran" vs "I am not a veteran")
    if re.search(r"\b(?:not|no|non)[-\s]+(?:a\s+)?(?:protected\s+)?veteran\b", a) and \
       re.search(r"\b(?:not|no|non)[-\s]+(?:a\s+)?(?:protected\s+)?veteran\b", b):
        return True
    # Negative disability status variants
    if re.search(r"\b(?:no|not|don'?t|do\s+not)\b.*?\bdisabilit", a) and \
       re.search(r"\b(?:no|not|don'?t|do\s+not)\b.*?\bdisabilit", b):
        return True
    # Gender variants
    if a in ("male", "man") and b in ("male", "man"):
        return True
    if a in ("female", "woman") and b in ("female", "woman"):
        return True
    # Ethnicity variants
    if "asian" in a and "asian" in b and "caucasian" not in a and "caucasian" not in b:
        return True
    return False


def workday_form_loading(snapshot: str) -> bool:
    """True while Workday has drawn the application shell but not its fields."""
    text = snapshot or ""
    has_application = bool(re.search(r"Application Progress|current step \d+ of \d+", text, re.IGNORECASE))
    has_forward_button = bool(re.search(r'button "Save and Continue"', text, re.IGNORECASE))
    has_loading_groups = bool(re.search(r": Loading\s*$", text, re.MULTILINE | re.IGNORECASE))
    has_field = bool(re.search(r"^- (textbox|combobox|listbox|radio|checkbox|spinbutton|slider)\b.*\[ref=",
                               text, re.MULTILINE | re.IGNORECASE))
    return has_application and has_forward_button and has_loading_groups and not has_field


def choices_in(snapshot: str, under: str = "", any_list: bool = False) -> list[tuple[str, str]]:
    """(reference, text) for every choice a list is showing.

    Some lists (Greenhouse's) give their options no name of their own and put
    the words in a child element, so the agent read them as empty and could
    choose nothing -- School, Discipline and Veteran Status were all left blank.

    `under` is the question being answered. R+L draws its dropdowns as a plain
    list, which is otherwise ignored here so that a job description's bullets
    are never mistaken for choices; a list named after the question at hand is
    that question's choices, and "Employer State or Province" could not be
    answered at all until it counted.
    """
    lines = (snapshot or "").splitlines()
    found: list[tuple[str, str]] = []
    holders: list[tuple[int, str]] = []   # the lists open on the page
    for i, line in enumerate(lines):
        m = _LINE.match(line)
        if not m:
            continue
        indent_here = len(m.group("indent"))
        while holders and holders[-1][0] >= indent_here:
            holders.pop()
        if m.group("role") in ("listbox", "menu", "grid", "tree", "combobox", "dialog"):
            holders.append((indent_here, m.group("role")))
        elif under and m.group("role") in ("list", "group", "region") \
                and _same_question(_unquote(m.group("name") or ""), under):
            holders.append((indent_here, m.group("role")))
        elif any_list and m.group("role") == "list":
            # R+L's dropdown is an unnamed list that appears when the widget is
            # opened. Only what was NOT on the page a moment ago counts, so a
            # job description's bullets are still never read as choices.
            holders.append((indent_here, m.group("role")))
        if m.group("role") not in OPTION_ROLES:
            continue
        # A plain bullet is not a choice: the job description's own list was
        # being read as the School dropdown's options.
        if m.group("role") in ("listitem", "gridcell") and not holders:
            continue
        ref = re.search(r"\[ref=([\w-]+)\]", m.group("attrs") or "")
        if not ref:
            continue
        words = [_unquote(m.group("name") or ""), _unquote(m.group("value") or "")]
        indent = len(m.group("indent"))
        for later in lines[i + 1:]:
            child = _LINE.match(later)
            if not child or len(child.group("indent")) <= indent:
                break
            words += [_unquote(child.group("name") or ""), _unquote(child.group("value") or "")]
        text = " ".join(w for w in (" ".join(words)).split() if w)[:160]
        if text:
            found.append((ref.group(1), text))
    return found


# What a dropdown that has no answer yet reads when the page draws it as a button
# ("Choose an option"): the words are a prompt, not a name, and its choices are
# not on the page until it is opened.
_PROMPT_NAME = re.compile(
    r"^\W*(choose( an option| one)?|select( an option| one| an item)?|please select|make a selection)\W*$",
    re.IGNORECASE)


def label_above(snapshot: str, ref: str) -> str:
    """The question a control answers, when the control's own name is only a
    prompt: the text just above it, in the same container -- ADP draws
    `generic: If you are under 18 ...?*` and then `button "Choose an option"`."""
    lines = (snapshot or "").splitlines()
    for i, line in enumerate(lines):
        if f"[ref={ref}]" not in line:
            continue
        indent = len(line) - len(line.lstrip())
        for earlier in reversed(lines[:i]):
            m = _LINE.match(earlier)
            if not m:
                continue
            depth = len(m.group("indent"))
            if depth > indent:
                continue                      # inside something drawn between them
            if depth < indent:
                break                         # left the container: nothing above belongs to it
            role = m.group("role")
            if role in ANSWER_ROLES or role in PRESS_ROLES:
                break                         # the previous control's own row
            text = _unquote(m.group("value") or m.group("name") or "")
            if role in ("generic", "text", "paragraph", "heading", "strong", "label", "legend") and text:
                return text[:200]
        return ""
    return ""


_CLICK_LABEL_JS = """e => {
  const label = (e.id && document.querySelector(`label[for="${CSS.escape(e.id)}"]`)) || e.closest('label')
      || (e.nextElementSibling && e.nextElementSibling.tagName !== 'INPUT' ? e.nextElementSibling : null);
  if (!label) throw new Error('no label beside the box');
  label.click();
}"""


def _is_checked(loc) -> Optional[bool]:
    """Whether a tick box shows as ticked -- a native box or one drawn with aria-checked."""
    try:
        return bool(loc.evaluate("e => e.checked === true || e.getAttribute('aria-checked') === 'true'"))
    except Exception:
        return None


def closest_choice(choices: list[str], wanted: str) -> Optional[int]:
    """The choice that says what the profile says, where the words differ.

    The profile says "I am not a veteran"; the form offers "I am not a
    protected veteran". A choice that reverses the meaning ("I identify as
    ...") is never taken: a yes/no in the answer must appear in the choice too.
    A town is the choice that names the same town in another spelling ("Springfield,
    IL" is "Springfield, Illinois, United States", and no other Springfield).
    """
    for i, choice in enumerate(choices):
        if geo_reference.same_locality(wanted, choice):
            return i
    want = set(_plain(wanted).split()) - {"i", "a", "an", "the", "of", "or", "am", "is", "are", "to", "my"}
    if not want:
        return None
    negative = bool(re.search(r"\b(not|no|never|none)\b", _plain(wanted)))
    best, best_score, tied = None, 0.0, False
    for i, choice in enumerate(choices):
        words = set(_plain(choice).split())
        if not words or negative != bool(re.search(r"\b(not|no|never|none)\b", _plain(choice))):
            continue
        score = len(want & words) / len(want)
        if score > best_score:
            best, best_score, tied = i, score, False
        elif score == best_score and best is not None:
            tied = True
    # Two choices as close as each other is no answer: "Asian" is equally "East Asian", "South Asian" and "Southeast
    # Asian", and the first was ticked (Lucid, 30 September).
    return best if best_score >= 0.6 and not tied else None


_PROSE = re.compile(r"^\s*- (paragraph|heading|text|strong|emphasis)\b")
_COMPLAINT = re.compile(r"required|issue|error|invalid|must be|cannot|please (enter|select|provide)", re.IGNORECASE)


def _shorten(line: str) -> str:
    """Prose is cut short, a control -- or a complaint about one -- is not."""
    if _PROSE.match(line) and not _COMPLAINT.search(line):
        return line[:200]
    return line[:400]


def compact_snapshot(snapshot: str, limit: int = 60_000) -> str:
    """The snapshot without its empty containers and link addresses, which
    carry no meaning for answering a form and cost most of the space."""
    kept = [_shorten(line) for line in _answered_lists_shown_short((snapshot or "").splitlines())
            if not re.match(r"^\s*- generic( \[active\])? \[ref=[\w-]+\]:?$", line)
            and not re.match(r"^\s*- /url:", line)]
    text = "\n".join(kept)
    return text if len(text) <= limit else text[:limit] + "\n... (page continues)"


def _answered_lists_shown_short(lines: list[str]) -> list[str]:
    """A list that already shows a real choice is sent with that choice alone; an open list keeps its choices.

    UKG, 30 September: most of the page sent to the AI was list choices -- 257 majors twice, every country and
    state, twelve months per date box -- and each page call took one to five minutes. The AI needs a list's
    choices only while it is still to be answered."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        head = re.match(r"^(\s*)- (?:combobox|listbox)\b", line)
        i += 1
        if not head:
            continue
        indent = len(head.group(1))
        options = []
        while i < len(lines) and len(lines[i]) - len(lines[i].lstrip()) > indent:
            options.append(lines[i])
            i += 1
        chosen = [o for o in options if "[selected]" in o and not option_match.is_placeholder(
            (re.search(r'option "([^"]*)"', o) or [None, ""])[1])]
        # A list drawn as a box shows its choice on its own line ('combobox "Degree": Master of Science (MS)').
        shown = line.rsplit("]:", 1)[1].strip() if "]:" in line else ""
        pad = re.match(r"^\s*", options[0]).group(0) if options else ""
        if (chosen or (shown and not option_match.is_placeholder(shown))) and len(options) > len(chosen) + 1:
            out += chosen + [f"{pad}- text: ({len(options) - len(chosen)} other choices)"]
        elif len(options) > _OPEN_LIST_SHOWN:
            # Still to be answered: enough choices to show what kind they are; the answer is matched against the
            # whole list when it is chosen (option_match), so it need not be copied from here.
            out += options[:_OPEN_LIST_SHOWN] + [f"{pad}- text: ({len(options) - _OPEN_LIST_SHOWN} more choices)"]
        else:
            out += options
    return out


_OPEN_LIST_SHOWN = 80


def answered_fields(controls: list[Control]) -> list[dict]:
    """{label, value} per question, for the safety checks -- a radio group as
    its question with the chosen option as the value."""
    fields: list[dict] = []
    groups: dict[str, str] = {}
    for c in controls:
        if c.role == "radio":
            if c.group and c.group not in groups:
                groups[c.group] = ""
            if c.checked and c.group:
                groups[c.group] = c.name
        elif c.role in ("textbox", "searchbox", "combobox", "listbox", "spinbutton"):
            fields.append({"label": c.question, "value": c.answer})
    fields += [{"label": g, "value": v} for g, v in groups.items()]
    return fields


# ---------------------------------------------------------------------------
# The plan Claude returns, as this module accepts it
# ---------------------------------------------------------------------------
@dataclass
class Answer:
    ref: str
    question: str
    action: str        # fill | choose | check | uncheck | upload_resume | upload_cover_letter
    value: str = ""
    source: str = ""   # profile.<field> | resume | owner_earlier_answer | job | consent | document


@dataclass
class PagePlan:
    page_kind: str = "other"
    step: str = ""
    answers: list[Answer] = field(default_factory=list)
    for_owner: list[dict] = field(default_factory=list)      # {question, reason, required}
    mismatches: list[dict] = field(default_factory=list)     # {question, on_page, facts_say}
    next_ref: str = ""
    next_label: str = ""
    next_kind: str = "none"   # next_step | final_submit | open_application | sign_in | consent | none

    @classmethod
    def from_json(cls, data: dict) -> "PagePlan":
        nxt = data.get("next") or {}
        return cls(
            page_kind=str(data.get("page_kind") or "other"),
            step=str(data.get("step") or ""),
            answers=[Answer(ref=str(a.get("ref") or ""), question=str(a.get("question") or ""),
                            action=str(a.get("action") or ""), value=str(a.get("value") or ""),
                            source=str(a.get("source") or ""))
                     for a in data.get("answers") or [] if isinstance(a, dict)],
            for_owner=[o for o in data.get("leave_for_owner") or [] if isinstance(o, dict)],
            mismatches=[o for o in data.get("mismatches") or [] if isinstance(o, dict)],
            next_ref=str(nxt.get("ref") or ""), next_label=str(nxt.get("label") or ""),
            next_kind=str(nxt.get("kind") or "none"),
        )


@dataclass
class Outcome:
    kind: str                  # submitted | owner_needed | no_sponsorship | captcha | gave_up
    page: object
    reasons: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return " | ".join(self.reasons) or self.kind


# A choice that says "none of these": it excludes every other choice of the same question.
_NONE_CHOICE = re.compile(
    r"^\s*(?:none(?:\s+of\s+(?:the\s+)?(?:above|these))?|n/?a|not\s+applicable|no\s+experience|"
    r"i\s+(?:have|possess)\s+no\b|i\s+do\s+not\s+have\b|i\s+don'?t\s+have\b|no\s+(?:prior\s+)?experience\b)",
    re.IGNORECASE)


def _without_contradicting_none(answers: list, by_ref: dict) -> list:
    """A question given several picks keeps the real ones and drops a "none" pick among them.

    Premier Health, 1 October: "I have experience in the following areas" was ticked "I have no experience in the
    following areas" together with "System Analysis/Installing/Design" and "Security" -- the page plan proposed all
    three. A "none" choice stands only when it is the question's only pick."""
    def key(a):
        control = by_ref.get(a.ref)
        return " ".join(((control.question if control else "") or a.question or "").lower().split())

    groups: dict = {}
    for a in answers:
        if a.action in ("check", "choose"):
            groups.setdefault(key(a), []).append(a)
    dropped = set()
    for question, picks in groups.items():
        if len(picks) < 2 or not question:
            continue
        nones = [a for a in picks if _NONE_CHOICE.search(a.value or "")
                 or _NONE_CHOICE.search(getattr(by_ref.get(a.ref), "name", "") or "")]
        if nones and len(nones) < len(picks):
            for a in nones:
                dropped.add(id(a))
                logger.info("DROPPED: %r for %r -- a 'none' choice cannot stand beside %d real one(s)",
                            (a.value or "")[:50], question[:50], len(picks) - len(nones))
    return [a for a in answers if id(a) not in dropped]


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------
class PageAgent:
    def __init__(self, assistant, claude, config, profile, resume, job, tracker=None, key: str = "",
                 job_dir: Optional[Path] = None, resume_file: Optional[Path] = None,
                 cover_letter: Optional[Callable[[], Optional[tuple[Path, Path]]]] = None):
        self.assistant, self.claude, self.config, self.profile = assistant, claude, config, profile
        self.resume, self.job, self.tracker, self.key = resume, job, tracker, key
        self.job_dir = Path(job_dir) if job_dir else None
        self.resume_file = resume_to_attach(resume_file, config)
        self.cover_letter = cover_letter
        self.resume_uploaded = False
        self._resume_autofill_attempted = False
        self.final_pressed = False
        self.pages_read = 0
        self.written: dict[str, str] = {}          # question -> value the agent put there
        self.failed: list[str] = []                # answers tried and not given; answer_what_is_known runs before apply_answers resets it
        self._letter: Optional[tuple[Path, Path]] = None
        self._letter_attached = False
        self._code_tries = 0
        self._last_code = ""
        self._pressed: dict[str, int] = {}
        self._opened_entries: set[str] = set()
        self._woken: set[str] = set()
        self._peeked: dict[str, int] = {}      # times a hidden dropdown list was opened to read it
        self._list_retries = 0
        self._refreshed = False
        self._shapes: dict[int, int] = {}
        self._asked_for_new_code = False
        self._signed_in_at: set[str] = set()
        self._google_tried: set[str] = set()
        self._retried_after_error = False
        self._google_failed: set[str] = set()   # sites whose Google account they will not accept
        self._google_reloads: dict[str, int] = {}   # times a site's dead Google button was answered with a fresh load
        self._emailed_in: set[str] = set()      # sites where the email has been given on its own page
        self._created_at: set[str] = set()      # sites where an account has been created
        # Sites that took the email and then asked for its password: they know the account. Kept when a run
        # resumes (unlike _emailed_in), because it is what the site said, not something the agent tried.
        self._account_known: set[str] = set()
        # Questions whose answer from the profile is not among their choices: they go to the AI (or the owner) with
        # the choices, instead of the same answer failing on every pass (Lucid, 29 September: three passes).
        self._misfit: set[tuple[str, str]] = set()   # (question, the answer its list refused)
        self._reset_asked: set[str] = set()     # sites where a reset to the ATS password was asked for this run
        self._verify_asked: set[str] = set()    # sites whose emailed verification link was looked for this run
        self.account_blocker = ""               # what only the owner can do at the account step, or ""
        self._account_seen: dict[str, tuple] = {}   # the last account state logged, per site
        self._account_waits: dict[str, int] = {}    # reads of an account step still drawing its form
        self._last_press_errors: tuple = ()          # what the page said was wrong after the last press
        self._run_stamp = time.strftime("%Y%m%d_%H%M%S")   # this run's folder for its page recordings
        self.required_left: list[str] = []            # required questions the AI could not be asked about
        self.unanswered_path: Path = UNANSWERED_FILE
        self._logged_unanswered: set[tuple[str, str]] = set()   # (question, site) already counted this run
        self._profile_answer_library: Optional[dict] = None
        self.notes: list[str] = []                 # worth telling the owner, not worth stopping for
        self.corrected: set[str] = set()           # questions the agent put right from the profile
        self.owner_answers: dict[str, str] = {}    # answers the owner set while the agent waited
        self._paused_state: dict[str, str] = {}
        self._pending_memories: list[dict] = []
        self._choice_methods: dict[str, str] = {}
        self._chosen_instead: dict[str, str] = {}     # ref -> the owner-approved alternative chosen in place of the value
        self._reused_recipes: dict[str, tuple[str, str, str, str, str]] = {}
        self._current_host = ""

    @staticmethod
    def fill_and_dispatch(locator, value: str, timeout: Optional[float] = None, **kwargs):
        """Fills an input and immediately dispatches bubbling input, change, and blur synthetic events."""
        return fill_and_dispatch(locator, value, timeout=timeout, **kwargs)

    @staticmethod
    def wipe_and_enforce_location_sweep(
        page,
        scope: Optional[Any] = None,
        profile: Optional[Any] = None,
        country: Optional[str] = None,
        state: Optional[str] = None,
        city: Optional[str] = None,
        record_callback: Optional[Callable[[str, str], None]] = None,
    ):
        return wipe_and_enforce_location_sweep(
            page,
            scope=scope,
            profile=profile,
            country=country,
            state=state,
            city=city,
            record_callback=record_callback,
        )

    def forget_sign_in_attempts(self, owner_acted: bool = True) -> None:
        """Google stays refused where the site itself rejected the account.

        `owner_acted` is False when the run resumed for a reason that says nothing about the
        account (a code reload, a refresh): a hold on the sign-in is then left where it is."""
        """A resumed run tries signing in again: the owner has had a hand in it,
        and the page may now offer something the last attempt never saw."""
        # What the site rejected is remembered; only the password attempts are
        # forgiven, so a resumed run can try them again.
        self._signed_in_at = set()
        self._emailed_in = set()
        self._pressed = {}          # a resumed run may press on again
        # The owner has looked at whatever held the sign-in back (a rejection, an account to verify):
        # it may try again. The day's limit on rejected sign-ins still applies (login_guard).
        if owner_acted:
            login_guard.owner_resumed(getattr(self, "_current_host", "") or "")
        self._google_reloads = {}   # ... and give a dead Google button its fresh loads again
        self.account_blocker = ""
        self._account_seen = {}
        self._account_waits = {}
        self._retried_after_error = False
        self._code_tries = 0        # a resumed run may fetch a fresh code
        self._asked_for_new_code = False
        # A resumed run gets a fresh page-read budget: the per-shape counter
        # otherwise carries the old total and trips the "keeps asking the same
        # things" guard on the very first read after a reload.
        self._shapes = {}
        self._woken = set()
        self._peeked = {}
        self._opened_entries = set()
        self._list_retries = 0
        # The loop guard's three tries are for one stretch of the run. Left as it
        # was, a guard that had tripped tripped again on the first press after the
        # owner's Continue ("identical across 4 consecutive cycles"), whatever had
        # been put right in between.
        guard = getattr(self, "_circuit_breaker", None)
        if guard is not None:
            guard.reset()
        # Re-read the answer library: the owner may have added answers to
        # data/profile_answers.json while the run waited.
        self._profile_answer_library = None

    def _ensure_state(self) -> None:
        """Code reloaded into a run that is already going keeps its old object:
        anything added since starts empty rather than failing."""
        for name, default in (("notes", list), ("corrected", set), ("owner_answers", dict), ("failed", list),
                              ("_entries", dict), ("_entry_blank", set), ("history", dict), ("_attached_here", set),
                              ("_google_tried", set), ("_retried_after_error", bool), ("_google_failed", set),
                              ("_google_reloads", dict), ("_peeked", dict),
                              ("_emailed_in", set), ("_created_at", set), ("_account_known", set), ("_reset_asked", set), ("_verify_asked", set), ("_misfit", set), ("_letter_attached", bool), ("_code_tries", int), ("_last_code", str), ("_asked_for_new_code", bool), ("_pressed", dict), ("_opened_entries", set), ("_woken", set), ("_list_retries", int), ("_shapes", dict),
                              ("_refreshed", bool),
                              ("_paused_state", dict), ("_signed_in_at", set), ("written", dict),
                              ("_profile_answer_library", lambda: None),
                              ("_resume_autofill_attempted", bool), ("_pending_memories", list),
                              ("_choice_methods", dict), ("_reused_recipes", dict), ("_chosen_instead", dict),
                              ("_current_host", str), ("account_blocker", str), ("_account_seen", dict),
                              ("_account_waits", dict), ("_last_press_errors", tuple),
                              ("_run_stamp", lambda: time.strftime("%Y%m%d_%H%M%S")), ("required_left", list)):
            if not hasattr(self, name):
                setattr(self, name, default())
        # A degree's dates the resume reader did not keep come from the profile (repeated_entries.with_profile);
        # here as well as where the run starts, so a run that is already going gets them on Resume.
        if self.history and getattr(self, "profile", None) is not None:
            self.history = repeated_entries.with_profile(self.history, self.profile)

    # -- the page --------------------------------------------------------------
    @staticmethod
    def tab(page):
        return getattr(page, "top", None) or page

    def snapshot(self, page) -> str:
        # The one place the page is read: a secret box's value never leaves it (it is saved to disk
        # and handed to the planner from here).
        return hide_secrets(self.tab(page).locator("body").aria_snapshot(mode="ai", timeout=20_000))

    def locate(self, page, ref: str):
        return self.tab(page).locator(f"aria-ref={ref}")

    def settle(self, page, ms: int = 1_500) -> None:
        tab = self.tab(page)
        try:
            tab.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:
            pass
        try:
            tab.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
        tab.wait_for_timeout(ms)

    def newest_tab(self, page, tabs_before: int):
        tab = self.tab(page)
        try:
            pages = tab.context.pages
            if len(pages) > tabs_before:
                logger.info("It opened in a new tab; continuing there")
                return pages[-1]
        except Exception:
            pass
        return tab

    # -- signing in ------------------------------------------------------------------
    GOOGLE_SIGN_IN = account_state.GOOGLE_SIGN_IN     # one wording, the account step's
    # ADP's Google button is sometimes dead for a whole page load (its script throws
    # "Cannot read properties of undefined (reading 'googlePlusSocialURL')" and every
    # press is ignored); a fresh load of the page brings it back. This many fresh loads,
    # then the site's own sign-in.
    GOOGLE_RELOADS = 2

    def _google_still_offered(self, page) -> Optional[Control]:
        """The Google sign-in the page still offers, or None once it offers none.

        This is what tells a sign-in that worked from one that did not: the site
        stops offering Google when it has taken the account. A page that cannot
        be read this moment (it is loading) proves nothing, so it counts as
        still offering it.
        """
        try:
            controls = parse_snapshot(self.snapshot(page))
        except Exception:
            return Control(ref="", role="button", name="Sign in with Google")
        return next((c for c in controls if c.role in PRESS_ROLES and self.GOOGLE_SIGN_IN.search(c.name or "")), None)

    def sign_in_step(self, page, controls: list[Control]) -> bool:
        """Signs in where the page asks for it. True when something was done.

        What the page is, and what to do about it, is decided in account_state: one reading of the page and
        one table, instead of each step below reading the page its own way (KBI, 28 September). The actions
        are the agent's own. Google first, always, when the site offers it -- the owner's standing
        instruction; LinkedIn, Indeed, Facebook, Apple and Microsoft are never used; otherwise the
        employer-account password, once per site. Whatever only the owner can do is set in
        `account_blocker`, and the run stops for it rather than reading the sign-in page as a form.
        """
        tab = self.tab(page)
        email = getattr(self.config, "ats_email", "") or getattr(self.profile, "email", "")
        host = urlparse(tab.url).netloc
        self.account_blocker = ""
        try:
            snapshot = self.snapshot(page)
        except Exception:
            return False
        state = account_state.read_state(snapshot, password_boxes=self._password_boxes(tab))
        try:
            candidate_state = self.assistant.adapter(tab).candidate_account_state(tab)
        except Exception:
            candidate_state = ""
        if candidate_state == "verification" and state.kind != account_state.CODE_ENTRY:
            state = account_state.AccountState(account_state.VERIFY_EMAIL, "the site's own account page says so",
                                               state.google_offered, state.password_boxes, state.form_error)
        try:
            exists = self.assistant.account_on_record() is True
        except Exception:
            exists = False
        memory = account_state.Memory(
            google_tried=host in self._google_tried, google_refused=host in self._google_failed,
            created=host in self._created_at, signed_in_tried=host in self._signed_in_at,
            email_given=host in self._emailed_in, account_exists=exists or host in self._account_known,
            reset_tried=host in self._reset_asked,
            verify_tried=host in self._verify_asked,
            refused_before=bool(email) and login_guard.refused_before(host, email),
            held=str(getattr(self.assistant, "_login_paused", "") or ""))
        step = account_state.next_step(state, memory)
        self._note_account_state(page, host, state, step)

        # Never an account on a job board, with Google or a password: the owner applies on the employer's own site
        # (job_sources.job_board). Adzuna, 30 September: its easy-apply sat behind an Adzuna login.
        board = job_sources.job_board(tab.url) if step.action not in (account_state.NOTHING, account_state.WAIT) else ""
        if board:
            self.account_blocker = board
            return False
        if step.action == account_state.FOR_OWNER:
            self.account_blocker = step.why
            return False
        if step.action == account_state.VERIFY_BY_LINK:
            self._verify_asked.add(host)
            verify = getattr(self.assistant, "verify_account_by_email_link", None)
            try:
                verified = bool(verify and verify(tab))
            except Exception as exc:
                logger.warning("VERIFY_LINK: %s", str(exc).splitlines()[0][:120])
                verified = False
            if verified:
                # A fresh sign-in is due: the one refused was for an account not yet verified.
                self._signed_in_at.discard(host)
                try:
                    tab.reload(wait_until="domcontentloaded", timeout=30_000)
                except Exception:
                    pass
                self.settle(page, 2_000)
                return True
            self.account_blocker = str(getattr(self.assistant, "_login_paused", "") or "") \
                or account_state.next_step(state, replace(memory, verify_tried=True)).why
            return False
        if step.action == account_state.RESET_PASSWORD:
            if email and self._reset_refused_password(tab, host, email):
                return True
            # Why the reset stopped, in the assistant's words; else what the table says now that it was tried.
            self.account_blocker = str(getattr(self.assistant, "_login_paused", "") or "") \
                or account_state.next_step(state, replace(memory, reset_tried=True)).why \
                or "the site refused the password and the reset to your ATS password did not go through: set it " \
                   "on the site to the password in Settings, then press Continue"
            return False
        if step.action == account_state.WAIT:
            waited = self._account_waits.get(host, 0)
            if waited >= 3:
                self.account_blocker = "the account step never showed its form: look at the page, then press Continue"
                return False
            self._account_waits[host] = waited + 1
            self.settle(page, 3_000)
            return True
        if step.action == account_state.OPEN_SIGN_IN:
            logger.info("LOGIN: %s -- opening sign-in instead of making another account", step.why)
            try:
                return bool(self.assistant._goto_login_page(tab) or self._press_named(tab, r"^\s*(sign in|log in)\s*$"))
            except Exception as exc:
                logger.warning("LOGIN: could not open sign-in (%s)", str(exc).splitlines()[0][:100])
                return False
        if step.action == account_state.GOOGLE and email:
            return self._sign_in_with_google_step(page, tab, host, email, controls)
        if step.action == account_state.GIVE_EMAIL and email:
            return self._give_email_first(page, host, email, controls)
        if step.action in (account_state.CREATE, account_state.SIGN_IN):
            making_account = step.action == account_state.CREATE
            (self._created_at if making_account else self._signed_in_at).add(host)
            if not making_account and host in self._emailed_in:
                self._account_known.add(host)       # it took the email and asked for this account's password
            logger.info("LOGIN: %s on %s", "creating the account" if making_account else "signing in", host)
            try:
                self.assistant._last_login_rejected = False     # only a refusal of this attempt counts below
                if self.assistant.handle_auth_gate(tab, email):
                    return True
            except Exception as exc:
                logger.warning("LOGIN: %s", str(exc).splitlines()[0][:120])
            # The password just typed was refused: what to do about a refusal is the table's (account_state),
            # asked now rather than after the sign-in page has been read as a form of questions for the owner.
            if not making_account and getattr(self.assistant, "_last_login_rejected", False):
                refused = account_state.AccountState(account_state.WRONG_PASSWORD, "the site refused the password")
                known = replace(memory, account_exists=memory.account_exists or host in self._account_known)
                if account_state.next_step(refused, known).action == account_state.RESET_PASSWORD \
                        and self._reset_refused_password(tab, host, email):
                    return True
            # Held back on purpose (login_guard): the owner is told why, and what to do.
            reason = str(getattr(self.assistant, "_login_paused", "") or "")
            if reason:
                note = f"sign-in on {host} is paused to protect the account: {reason}"
                if note not in self.notes:
                    self.notes.append(note)
                    logger.info("LOGIN: %s", note[:200])
                self.account_blocker = note
            return False
        return False

    def _reset_refused_password(self, tab, host: str, email: str) -> bool:
        """Resets a refused password to the same ATS password with the code emailed to the owner, and signs in
        (CLAUDE.md §5). Asked for once per site per run; the limits on it (employer sites only, the owner's
        permission to read the mail, no CAPTCHA, login_guard) are the assistant's."""
        self._reset_asked.add(host)
        recover = getattr(self.assistant, "recover_rejected_sign_in", None)
        if recover is None:
            return False
        try:
            if recover(tab, email):
                logger.info("LOGIN: the password was reset to your ATS password and the sign-in went through on %s",
                            host)
                return True
        except Exception as exc:
            logger.warning("RECOVERY: %s", str(exc).splitlines()[0][:120])
        return False

    def _note_account_state(self, page, host: str, state, step) -> None:
        """Logs each change of account state, and saves a screenshot and the page text for it.

        KBI's run stopped on its sign-in step and left nothing to show which page it was on."""
        seen = (state.kind, step.action)
        if self._account_seen.get(host) == seen or state.kind == account_state.NONE:
            return
        self._account_seen[host] = seen
        logger.info("ACCOUNT_STATE: %s -> %s%s", state, step.action, f" ({step.why[:100]})" if step.why else "")
        if self.job_dir is None:
            return
        try:
            folder = Path(self.job_dir) / "account"
            folder.mkdir(parents=True, exist_ok=True)
            stem = folder / f"{time.strftime('%Y%m%d_%H%M%S')}_{state.kind}"
            evidence.screenshot(self.tab(page), stem.with_suffix(".png"))
            stem.with_suffix(".txt").write_text(f"{self.tab(page).url}\n{state}\n-> {step.action} {step.why}\n\n"
                                                + self.snapshot(page), encoding="utf-8")
        except Exception as exc:
            logger.debug("Could not save the account page: %s", str(exc).splitlines()[0][:100])

    @staticmethod
    def _press_named(tab, pattern: str) -> bool:
        """Presses the first visible button or link whose name, as a person reads it, matches."""
        wanted = re.compile(pattern, re.IGNORECASE)
        for role in ("button", "link"):
            found = tab.get_by_role(role, name=wanted)
            for i in range(min(found.count(), 5)):
                if found.nth(i).is_visible():
                    found.nth(i).click(timeout=8_000)
                    return True
        return False

    def _sign_in_with_google_step(self, page, tab, host: str, email: str, controls: list[Control]) -> bool:
        """Google sign-in, found by the words a person reads on the button.

        OCC's "Sign in with Google" button carries its words inside it, not as a label, and was not found."""
        self._google_tried.add(host)
        logger.info("LOGIN: this site offers Google sign-in -- using it")
        try:
            google = next((c for c in controls if c.role in PRESS_ROLES and self.GOOGLE_SIGN_IN.search(c.name or "")),
                          None)
            if google is not None:
                button = self.locate(page, google.ref).element_handle(timeout=5_000)
            else:
                # A button, or a link (Adzuna's "Login with Google" is a link).
                found = tab.get_by_role("button", name=self.GOOGLE_SIGN_IN).or_(
                    tab.get_by_role("link", name=self.GOOGLE_SIGN_IN))
                button = found.first.element_handle(timeout=5_000)
            if self.assistant._sign_in_with_google(tab, button, email, host, lambda: self._google_still_offered(page)):
                logger.info("LOGIN: signed in with Google")
            elif getattr(self.assistant, "google_press_ignored", False) \
                    and self._google_reloads.get(host, 0) < self.GOOGLE_RELOADS:
                # The site did not react at all: its button is dead until the
                # page is loaded again. That says nothing about the account.
                self._google_reloads[host] = self._google_reloads.get(host, 0) + 1
                self._google_tried.discard(host)
                logger.info("LOGIN: %s did not react to its Google button -- loading the page again (%d of %d)",
                            host, self._google_reloads[host], self.GOOGLE_RELOADS)
                tab.reload(wait_until="domcontentloaded", timeout=30_000)
                self.settle(page, 3_000)
            else:
                # The site would not take it (NVIDIA: "Account is Inactive").
                # Its own sign-in is used from here on.
                self._google_failed.add(host)
                self.assistant.google_refused_on = set(self._google_failed)
                logger.info("LOGIN: %s did not accept the Google account -- using its own sign-in", host)
            return True
        except Exception as exc:
            logger.warning("LOGIN: Google sign-in did not go through (%s)", str(exc).splitlines()[0][:100])
            return True   # the page has changed; read it again

    def _give_email_first(self, page, host: str, email: str, controls: list[Control]) -> bool:
        """A sign-in that asks for the email on its own page first (Workday's does, after "Sign in with
        email"): give it, and press on. The password is never typed here -- that is handle_auth_gate's."""
        box = next((c for c in controls if c.role in ("textbox", "searchbox") and not c.answer
                    and re.search(r"e-?mail|user ?name", f"{c.name} {c.question}", re.IGNORECASE)), None)
        if box is None:
            return False
        self._emailed_in.add(host)
        logger.info("LOGIN: this sign-in asks for the email first -- giving it")
        try:
            fill_and_dispatch(self.locate(page, box.ref), email, timeout=8_000)
            go = next((c for c in controls if c.role in PRESS_ROLES and re.match(
                r"^(sign ?in|log ?in|continue|next|submit)$", " ".join((c.name or "").split()), re.IGNORECASE)), None)
            if go is not None:
                self.locate(page, go.ref).click(timeout=8_000)
            else:
                self.locate(page, box.ref).press("Enter", timeout=5_000)
            return True
        except Exception as exc:
            logger.warning("LOGIN: could not give the email (%s)", str(exc).splitlines()[0][:100])
            return False

    # A code emailed to the owner for their own account or email address.
    ACCOUNT_CODE = re.compile(
        r"(verification|security|confirmation|one[- ]?time|access)\s*code|passcode|\botp\b|"
        r"code (was )?(sent|emailed) to", re.IGNORECASE)

    # A section for the owner's jobs or degrees, the record that fills it, the site filler's name, and the labels the
    # site's error list names for a box of it.
    _ENTRY_SECTIONS = (
        ("Work Experience", "experience", "fill_experience_section",
         ("job title", "company", "location", "from", "to", "role description")),
        ("Education", "education", "fill_education_section",
         ("school or university", "school", "degree", "field of study", "from", "to (actual or expected)",
          "overall result (gpa)")),
    )

    def add_entries_the_site_way(self, page, snapshot: str) -> bool:
        """A section for the owner's jobs or degrees that is still empty -- its heading and its own Add button, no
        entry yet -- is filled by the site's own entry filler from the owner's record (Workday: sites/workday.py
        enters each job's and degree's boxes, dates and lists the way Workday takes them).

        Ciena on Workday, 30 September: the page agent had no such step -- only the older flow and the dashboard's
        'fill experience' signal called the filler -- so My Experience was left with no jobs and no degrees, and the
        run went round the page until the loop guard stopped it. Once per section per run; a site without a filler
        is left to the page as before."""
        tab = self.tab(page)
        host = host_of(tab.url)
        history = getattr(self, "history", {}) or {}
        done = self.__dict__.setdefault("_entries_added", set())
        redone = self.__dict__.setdefault("_entries_redone", set())
        try:
            adapter = self.assistant.adapter(tab)
        except Exception:
            return False
        # The fields the site's own error list names ("Error - School or University"), lower case.
        refused = {m.strip().lower() for m in re.findall(r"Error\s*[-\u2013]\s*([A-Za-z][A-Za-z /()]{1,40}?)(?=[\"\n:]|\s+The\b)",
                                                         snapshot or "")}
        added = False
        for heading, record, filler, labels in self._ENTRY_SECTIONS:
            entries = [e for e in (history.get(record) or []) if isinstance(e, dict)]
            if not entries or not hasattr(adapter, filler) or not hasattr(adapter, "entry_count"):
                continue
            if not re.search(rf'- heading "{re.escape(heading)}"', snapshot or "", re.IGNORECASE):
                continue
            shown = adapter.entry_count(tab, heading)
            if shown is None:
                continue
            if shown == 0:
                if (host, heading) in done:
                    continue
                done.add((host, heading))
                logger.info("ENTRIES: %s is empty -- adding your %d %s the site's way", heading, len(entries),
                            "jobs" if record == "experience" else "degrees")
            else:
                # Entries the site refused, or more or fewer than the record holds (an extra, empty one): the site's
                # filler clears the section and enters the record again -- once per section per run. Ciena, 30
                # September: an extra empty degree and a job's start month kept Save and Continue refused.
                wrong = shown != len(entries) or bool(refused & set(labels))
                if not wrong or (host, heading) in redone:
                    continue
                redone.add((host, heading))
                logger.info("ENTRIES: %s shows %d of your %d %s%s -- entering them again the site's way", heading,
                            shown, len(entries), "jobs" if record == "experience" else "degrees",
                            " and the site refused some" if refused & set(labels) else "")
            try:
                getattr(self.assistant, filler)(tab, entries)
                added = True
            except Exception as exc:
                logger.warning("ENTRIES: could not add the %s entries: %s", heading, str(exc).splitlines()[0][:120])
        return added

    def open_entry_for_missing_field(self, page, snapshot: str, controls: list[Control]) -> bool:
        """Opens a collapsed entry the page is complaining about.

        R+L lists each job and each qualification as a closed card and then
        says "The Reason for Leaving field is required" -- the box is inside
        the card, and there is nothing on the page to fill until it is opened.
        """
        errors = " ".join(re.findall(r"- alert[^:\n]*: (.+)", snapshot))
        wanted = re.findall(r"The ([A-Za-z/ ]{3,40}?) field is required", errors)
        if not wanted:
            return False
        missing = [w for w in dict.fromkeys(wanted)
                   if not any(_same_question(c.question, w) for c in controls)]
        if not missing:
            return False
        cards = [c for c in controls
                 if c.role in ("group", "region", "listitem") and not c.options and not c.name
                 and c.ref not in self._opened_entries]
        if not cards or len(self._opened_entries) >= 8:
            return False
        card = cards[0]
        self._opened_entries.add(card.ref)
        logger.info("The page wants %s, which is inside a closed entry -- opening it", ", ".join(missing[:2]))
        try:
            self.locate(page, card.ref).click(timeout=6_000)
            return True
        except Exception as exc:
            logger.debug("Could not open the entry: %s", str(exc).splitlines()[0][:100])
            return False

    def rejected_by_the_page(self, snapshot: str, controls: list[Control]) -> list[str]:
        """Fields the page still calls empty although they show a value.

        R+L kept saying "The Employer State or Province field is required"
        after the agent had typed VA into it: that box only takes a value
        picked from its own list.
        """
        if self._list_retries >= 2:
            return []
        errors = " ".join(re.findall(r"- alert[^:\n]*: (.+)", snapshot or ""))
        named = re.findall(r"The ([A-Za-z/ ]{3,40}?) field is required", errors)
        rejected = []
        for name in dict.fromkeys(named):
            shown = next((c for c in controls if _same_question(c.question, name) and c.answer), None)
            if shown is not None:
                rejected.append(f"{name} (it shows {shown.answer[:30]!r})")
        if rejected:
            self._list_retries += 1
        return rejected

    def follow_the_chosen_brain(self) -> None:
        """Who works out the answers -- the session the owner is talking to, or
        the API -- read afresh each page from AGENT_BRAIN.

        It is decided here rather than where the run starts because apply_flow
        cannot reload itself while it is running: this is what lets the owner
        move an application already half filled in from one to the other.
        """
        try:
            import os
            from dotenv import load_dotenv
            load_dotenv()
            wanted = (os.getenv("AGENT_BRAIN", "api") or "api").strip().lower()
        except Exception:
            return
        # Only the two real brains are swapped: a run given something else on
        # purpose (a test, a stub) keeps what it was given.
        if type(self.claude).__name__ not in ("ClaudeClient", "SessionPlanner"):
            return
        on_session = type(self.claude).__name__ == "SessionPlanner"
        if wanted == "session" and not on_session:
            try:
                import session_planner
                brain = session_planner.SessionPlanner(self.config, folder=Path("data"), fallback=self.claude)
                brain.now_applying = f"{getattr(self.job, 'company', '')} -- {getattr(self.job, 'title', '')}"
                self.claude = brain
                logger.info("BRAIN: asking the Claude Code session about each page (no API credit used)")
            except Exception as exc:
                logger.warning("Could not hand over to the session: %s", str(exc)[:120])
        elif wanted != "session" and on_session:
            self.claude = self.claude._fallback
            logger.info("BRAIN: back to asking the API about each page")

    def wake_loading_control(self, page, snapshot: str) -> bool:
        """Clicks a control that is still a spinner, so it draws itself.

        R+L's "How did you hear about us?" stays a "Loading screen" until it is
        touched, so there was never anything to choose and the question came
        back to the owner.
        """
        spinners = re.findall(r'- progressbar "([^"]{3,80})"[^\n]*\[ref=([\w-]+)\]', snapshot or "")
        for name, ref in spinners:
            if ref in self._woken or len(self._woken) >= 4:
                continue
            self._woken.add(ref)
            logger.info("Waking %r, which is still loading", name.replace(" Loading screen", "")[:50])
            try:
                self.locate(page, ref).click(timeout=5_000)
                return True
            except Exception as exc:
                logger.debug("Could not wake it: %s", str(exc).splitlines()[0][:80])
        return False

    def _code_boxes(self, page) -> list[Control]:
        """The code boxes the page is showing now."""
        return [c for c in parse_snapshot(self.snapshot(page))
                if c.role in ("textbox", "searchbox", "spinbutton") and not c.disabled
                and self.ACCOUNT_CODE.search(f"{c.question} {c.container}")]

    def complete_account_code(self, page, controls: list[Control], snapshot: str) -> bool:
        """Enters a one-time code emailed to the owner for their own account.

        The owner approved this on 2026-09-15 for account setup and sign-in
        (browser_automation.complete_emailed_passcode), and R+L Carriers' form
        asks for exactly that; so does Greenhouse's "enter the code to confirm
        you're a human", which the owner decided on 2026-09-25 the agent enters
        too. Whether it may is emailed_codes.why_not: a CAPTCHA on the page (as
        beside Harbinger's code) keeps it the owner's, the site's wording does not.
        """
        # R+L Carriers gives the code a box per digit ("Enter verification code
        # digit 1 of six."), so one box or six, they are all the same step.
        boxes = [c for c in controls
                 if c.role in ("textbox", "searchbox", "spinbutton") and not c.answer and not c.disabled
                 and self.ACCOUNT_CODE.search(f"{c.question} {c.container}")]
        if not boxes or self._code_tries >= 3:
            return False
        why = emailed_codes.why_not(self.profile, page.url, captcha=safety.captcha_visible(page),
                                    email=getattr(self.config, "ats_email", "") or "")
        if why:
            logger.info("CODE: a code was emailed to you, but %s", why)
            return False
        self._code_tries += 1
        logger.info("CODE: a code was emailed to you for this account -- fetching it from your mail")
        tab = self.tab(page)
        try:
            # A different code each time: the last one was refused.
            expected = len(boxes) if len(boxes) > 1 else 0
            code = self.assistant.passcode_from_gmail(tab, previous=self._last_code, length=expected)
            if code and expected:
                # It must fit the boxes: a number picked out of the wrong part
                # of the message was typed in and refused ("The code isn't
                # valid. Enter a valid code.").
                digits = re.sub(r"\D", "", code)
                code = digits if len(digits) == expected else ""
                if not code:
                    logger.info("CODE: your mail doesn't show a %d-digit code yet", expected)
                    return False
            if not code and self._last_code and not self._asked_for_new_code:
                # The last code was refused and no newer mail has come: ask the
                # site to send another, then read for it.
                fresh = next((c for c in controls if c.role in PRESS_ROLES and re.search(
                    r"send (a )?new code|resend( code)?|send( me)? (another|a new) code",
                    c.name or "", re.IGNORECASE)), None)
                if fresh is not None:
                    self._asked_for_new_code = True
                    logger.info("CODE: asking the site to send a new one")
                    self.locate(page, fresh.ref).click(timeout=8_000)
                    tab.wait_for_timeout(4_000)
                    code = self.assistant.passcode_from_gmail(tab, previous=self._last_code, length=expected)
            if not code:
                logger.info("CODE: it has not arrived yet -- the agent will look again")
                return False
            self._last_code = code
            # The page redraws while a new code is sent, so the boxes found
            # earlier are gone: find them again before typing.
            boxes = self._code_boxes(page) or boxes
            for box in (b for b in boxes if b.answer):   # clear only what was refused
                try:
                    self.locate(page, box.ref).fill("", timeout=2_000)
                except Exception:
                    pass
            first = self.locate(page, boxes[0].ref)
            first.click(timeout=5_000)
            if len(boxes) > 1:
                tab.keyboard.type(code, delay=150)   # a box per digit: they advance themselves
            else:
                fill_and_dispatch(first, code, timeout=5_000)
            tab.wait_for_timeout(800)
            press = next((c for c in parse_snapshot(self.snapshot(page))
                          if c.role in PRESS_ROLES and re.match(
                              r"^\s*(verify|continue|confirm|next|submit code)\s*$",
                              " ".join((c.name or "").split()), re.IGNORECASE)), None)
            if press is not None:
                self.locate(page, press.ref).click(timeout=8_000)
            logger.info("CODE: entered the code from your mail")
            return True
        except Exception as exc:
            logger.warning("Could not complete the emailed code: %s", str(exc).splitlines()[0][:120])
            return False

    # -- profile-only form planning -------------------------------------------------
    def uses_profile_planner(self) -> bool:
        """Whether this run must keep Claude out of the application form.

        Tests and existing assisted users that do not have the new setting keep
        their old planner.  AppConfig defaults the real application to
        ``profile`` mode.
        """
        return str(getattr(self.config, "form_answer_mode", "claude") or "claude").lower() == "profile"

    def _answer_library(self) -> dict:
        """Explicit answers for questions that are not represented by fields.

        ``data/profile_answers.json`` is intentionally local and ignored by
        Git.  Keys are exact question text, or ``re:<regular expression>``.
        Values are strings, numbers, booleans, or ``{"value": "..."}``.
        A value here is an owner-approved profile answer, not an LLM draft.
        """
        if self._profile_answer_library is not None:
            return self._profile_answer_library
        import profile_setup
        path = Path(profile_setup.ANSWERS_PATH)     # the one place that names the saved answers
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
            self._profile_answer_library = loaded if isinstance(loaded, dict) else {}
        except Exception as exc:
            logger.warning("Could not read %s: %s", path, str(exc).splitlines()[0][:120])
            self._profile_answer_library = {}
        return self._profile_answer_library

    def library_answer(self, question: str, exact_only: bool = False) -> str:
        """The explicitly saved answer matching *question*, if any."""
        for key, raw_value in self._answer_library().items():
            if not isinstance(key, str):
                continue
            is_regex = key.startswith("re:")
            if exact_only and is_regex:
                continue
            matches = _same_question(key, question) if not is_regex else False
            if is_regex:
                try:
                    matches = bool(re.search(key[3:], question or "", re.IGNORECASE))
                except re.error:
                    logger.warning("Ignoring invalid answer-library pattern %r", key[:80])
                    matches = False
            if not matches:
                continue
            value = raw_value.get("value", "") if isinstance(raw_value, dict) else raw_value
            if isinstance(value, bool):
                return "Yes" if value else "No"
            return str(value or "").strip()
        return ""

    def _attestation_answer(self, control: Control) -> tuple[str, str]:
        """The globally authorised consent/signature answer for a control.

        These are kept out of ``known_answer`` so ``sign()`` can still apply
        declarations last, after every ordinary field on the page is filled.
        """
        question = f"{control.question} {control.name}".strip()
        if safety.is_privacy_consent(question) and getattr(self.profile, "accept_application_privacy_prompts", False):
            return "checked", "profile.accept_application_privacy_prompts"
        if not safety.is_attestation(question) or not getattr(self.profile, "sign_attestations", False):
            return "", ""
        if control.role in ("textbox", "searchbox"):
            name = str(getattr(self.profile, "full_name", "") or "").strip()
            return (name, "profile.full_name") if name else ("", "")
        return "checked", "profile.sign_attestations"

    def profile_plan(self, controls: list[Control], required: set[str], still_open: list[str],
                     snapshot: str = "") -> PagePlan:
        """Build a deterministic page plan without calling Claude.

        Known profile data is handled before this method.  This second pass
        adds globally authorised consent/signature actions, identifies required
        gaps, and presses only a plainly-labelled application control.
        """
        answers: list[Answer] = []
        attestation_refs: set[str] = set()
        for control in controls:
            answerable_button = (control.role == "button" and control.GENERIC_NAMES.match(control.name.strip())
                                 and bool(control.container or control.group or control.context)) \
                or is_workday_choice_button(control)
            if (control.role not in ANSWER_ROLES and not answerable_button) or control.disabled or control.answer:
                continue
            value, source = self._attestation_answer(control)
            if not value or control.ref in attestation_refs:
                continue
            attestation_refs.add(control.ref)
            action = "fill" if control.role in ("textbox", "searchbox", "spinbutton") else "check"
            answers.append(Answer(control.ref, control.question, action, value, source))

        unresolved: list[dict] = []
        seen: set[str] = set()
        for open_question in still_open:
            question = open_question.rstrip("*").strip()
            if not question or question in seen:
                continue
            seen.add(question)
            control = next((c for c in controls if _same_question(c.question, question)), None)
            if control is None:
                continue
            is_required = open_question.endswith("*") or "*" in control.question or "*" in control.name \
                or any(_same_question(question, marked) for marked in required)
            known_val, _ = self.known_answer(control)
            has_known_answer = bool(known_val)
            if not is_required and not has_known_answer:
                continue
            value, _source = self._attestation_answer(control)
            if value:
                continue
            unresolved.append({
                "question": question,
                "reason": "could not fill known profile value automatically" if has_known_answer else "no matching profile field or data/profile_answers.json entry",
                "required": is_required or has_known_answer,
            })
        if unresolved:
            return PagePlan(page_kind="application_form", answers=answers, for_owner=unresolved)

        forward = self.profile_forward(controls)
        if forward is None:
            return PagePlan(page_kind="application_form", answers=answers)
        label = " ".join((forward.name or "").split())
        opening = bool(re.fullmatch(r"apply(?: now)?|start application|begin application", label, re.IGNORECASE))
        # The step counter the page shows ("Step 1 of 2"), as the AI plan reports it: a "Submit" on
        # a step with more to come only saves that step (Schwab's step 2 of 5). Without it, a page
        # answered from the profile alone called its "Submit" the last and stopped for a resume
        # that belonged to a later step.
        counter = current_step(snapshot)
        step = f"{counter[0]} of {counter[1]}" if counter else ""
        steps_remain = bool(counter) and counter[0] < counter[1]
        return PagePlan(
            page_kind="job_description" if opening else "application_form",
            step=step or "profile-only",
            answers=answers,
            next_ref=forward.ref,
            next_label=label,
            next_kind="open_application" if opening else (
                "final_submit" if safety.is_submit_label(label) and not steps_remain else "next_step"
            ),
        )

    def posting_plan(self, controls: list[Control], required: set[str], still_open: list[str],
                     snapshot: str = "") -> Optional[PagePlan]:
        """A job posting whose way on is a plain Apply: press it, without asking the AI.

        The posting page's own boxes -- a job search, a language picker, "email me jobs like this" -- looked like
        open questions, so the page went to the AI, which answered "click Apply" (Embry-Riddle, 28 September:
        three requests of a 20-a-day allowance). Nothing on a posting is the owner's to answer. Only when no
        question still open is required, so a form whose last button happens to say "Apply" still goes the
        usual way."""
        if any(_open_is_required(q, controls, required) for q in still_open):
            return None
        plan = self.profile_plan(controls, required, [], snapshot)
        if plan.next_kind != "open_application" or plan.for_owner:
            return None
        if still_open or _blank_choice_on_page(controls):
            logger.info("POSTING_PAGE: '%s' opens the application -- the page's other boxes are not questions, "
                        "no AI call", plan.next_label)
        return plan

    def profile_forward(self, controls: list[Control]) -> Optional[Control]:
        """The safest clearly-labelled application action on a local-plan page."""
        options = [c for c in controls if c.role in PRESS_ROLES and c.name and not c.disabled
                   and FORWARD_LABEL.match(" ".join(c.name.split())) and not NEVER_PRESS.search(c.name)]
        if not options:
            return None
        priority = ("apply now", "apply", "start application", "begin application", "continue", "next",
                    "review", "submit application", "submit")
        for wanted in priority:
            found = next((c for c in options if " ".join(c.name.split()).lower() == wanted), None)
            if found is not None:
                return found
        return options[0] if len(options) == 1 else None

    # -- facts a profile or assisted planner may answer from --------------------------
    def known_answer(self, control: Control) -> tuple[str, str]:
        """What the agent already knows for this box, and where it came from.

        The owner's profile first -- his own details and the answers he has
        settled once and for all -- then an answer he gave himself on an
        earlier application. Nothing about an employer, a school or a
        supervisor is answered this way: those belong to one entry, not to him.
        """
        question = control.question
        # Tick boxes of the owner's standing decision (30 September): agreements and acknowledgements are ticked
        # ("I have read and agree", "I acknowledge", "By checking this box", terms, privacy) -- a declaration only
        # with sign_attestations, the rest with accept_application_privacy_prompts; a text-message consent never,
        # unless the owner's preferred contact is text. Text messages are looked at first: "I agree to receive
        # text messages" is not an agreement to tick.
        if control.role in ("checkbox", "switch"):
            said = f"{control.question} {control.name} {control.container} {control.context}"
            if _TEXT_MESSAGES.search(said):
                prefers = str(getattr(self.profile, "preferred_contact_method", "") or "").strip().lower()
                return ("checked" if prefers in ("sms", "text", "text message") else "unchecked"), \
                    "profile.preferred_contact_method"
            if _AGREEMENT.search(said):
                if safety.is_attestation(said):
                    if getattr(self.profile, "sign_attestations", False):
                        return "checked", "profile.sign_attestations"
                elif getattr(self.profile, "accept_application_privacy_prompts", False):
                    return "checked", "profile.accept_application_privacy_prompts"
        if not question or safety.is_attestation(question):
            return "", ""

        # A box inside a repeated Work or Education entry belongs to that job or that degree: it is answered from
        # that entry's own record, never from one value for the whole person (Steelcase, 29 September: a job's
        # "End date" took the education end date, and both degrees took the first one's school).
        entry = getattr(self, "_entries", {}).get(control.ref)
        if entry is not None:
            value, source, blank = repeated_entries.answer(entry, getattr(self, "history", {}) or {})
            if blank:
                if not isinstance(getattr(self, "_entry_blank", None), set):
                    self._entry_blank = set()
                self._entry_blank.add(control.ref)
                shown = (control.value or entry.values.get(entry.label.lower()) or "").strip()
                if shown:
                    note = (f"{(repeated_entries.record_for(entry, self.history) or {}).get('company', 'your current job')} "
                            f"is your current job, but its {entry.label!r} shows {shown!r} -- clear it")
                    if note not in self.notes:
                        self.notes.append(note)
            return value, source

        # 0. Privacy & Recruiting Communications consent checkbox (authorized by profile.accept_application_privacy_prompts)
        if getattr(self.profile, "accept_application_privacy_prompts", True):
            combined_text = f"{control.question} {control.name} {control.container} {control.context}".strip()
            if (safety.is_privacy_consent(combined_text) or
                re.search(r"i agree to the privacy statement|privacy policy|privacy statement", combined_text, re.IGNORECASE)) \
                and not safety.is_attestation(combined_text):
                if control.role in ("checkbox", "switch"):
                    return "checked", "profile.accept_application_privacy_prompts"

        # 1. Saved exact answer library (e.g. from data/profile_answers.json) takes priority
        explicit = self.library_answer(question, exact_only=True)
        if explicit:
            # Who the owner is -- name, email, phone, address -- is the profile's to say, whatever a saved answer
            # holds: a saved "Last Name": "c" was typed into UKG's Last name (30 September).
            owned = concept_matcher.match_concept(question=question, container=control.container,
                                                  context=control.context, name=control.name)
            if owned in _PROFILE_OWNS:
                val, src = concept_matcher.resolve_profile_value(concept=owned, profile=self.profile,
                                                                 options=control.options, question=question)
                if val:
                    return val, src
            return explicit, "profile.answer_library"

        # 2. Universal Concept & Synonym Engine (zero-cost, zero-heavy-RAM, cross-ATS)
        concept = concept_matcher.match_concept(
            question=question,
            container=control.container,
            context=control.context,
            name=control.name,
        )
        # The options on offer settle what a location question asks.
        concept = concept_matcher.confirm_concept(concept, control.options)
        if concept:
            val, src = concept_matcher.resolve_profile_value(
                concept=concept,
                profile=self.profile,
                options=control.options,
                question=question,
            )
            if val:
                return val, src
            if concept == "MIDDLE_NAME":
                replacement = "N/A" if "*" in question or "required" in question.lower() else ""
                return replacement, "profile.middle_name"
            if str(src).startswith("profile.") and str(src).split(".", 1)[1] in BLANK_MEANS_NONE:
                return "", src               # the profile's word that there is none (no preferred name)

        # 3. Regex patterns from data/profile_answers.json
        regex_explicit = self.library_answer(question)
        if regex_explicit:
            return regex_explicit, "profile.answer_library"

        # 3b. "Have you worked at <company>?" -- from the person's own work history: "No" unless the company is
        # one they worked for (the owner's rule, 29 September).
        worked = employment_history.answer(question, getattr(self.job, "company", "") or "",
                                           self._past_employers())
        if worked:
            return worked, "profile.work_history"

        field = _name_field(question) or _detail_field(question)
        if field:
            value = str(getattr(self.profile, field, "") or "").strip()
            if value:
                return value, f"profile.{field}"
            if field == "middle_name":
                # An explicit empty profile value overrides historical answers.
                # Only required fields receive a placeholder.
                replacement = "N/A" if "*" in question or "required" in question.lower() else ""
                return replacement, f"profile.{field}"
        effective_q = f"{question} {control.group or ''} {control.container or ''}".strip()
        for pattern, standing in _STANDING_ANSWERS:
            if not pattern.search(effective_q):
                continue
            held = getattr(self.profile, standing, None)
            if isinstance(held, bool):
                if control.options and standing == "open_to_relocation" and held:
                    # "No, but willing to relocate" -- never "No, and not willing to relocate",
                    # which also has "relocate" in it.
                    for opt in control.options:
                        if re.search(r"\bwilling to relocate\b", opt, re.IGNORECASE) \
                                and not re.search(r"\b(not|un)\s*willing|\bnot\b", opt, re.IGNORECASE):
                            return opt, f"profile.{standing}"
                return ("Yes" if held else "No"), f"profile.{standing}"
            value = str(held or "").strip()
            if value:
                return value, f"profile.{standing}"
        # Where the owner lives, only when the question asks that: the concept matcher decides (a third, looser copy
        # here answered "What is the Country of your birth?" with the country of residence -- Forterra, 30 September).
        if concept_matcher.match_concept(question) == "COUNTRY":
            value = str(getattr(self.profile, "country", "") or "").strip()
            if value:
                return value, "profile.country"
        if re.search(r"mobile|cell|phone number", question, re.IGNORECASE) and not re.search(
                r"home|work|employer", question, re.IGNORECASE):
            value = str(getattr(self.profile, "phone_mobile", "") or getattr(self.profile, "phone", "") or "").strip()
            if value:
                return value, "profile.phone_mobile"
        if re.search(r"desired salary|salary desired|salary range", question, re.IGNORECASE):
            minimum = str(getattr(self.profile, "salary_min", "") or "").strip()
            maximum = str(getattr(self.profile, "salary_max", "") or "").strip()
            if minimum and maximum:
                return f"${int(float(minimum)):,} - ${int(float(maximum)):,}", "profile.salary_range"
        # A question that asks for the state, as the concept matcher reads it -- not any question with the word in
        # it: "has your professional license/certification (in any state) ever been revoked?" was answered
        # "Virginia" (Premier Health, 1 October).
        if concept_matcher.match_concept(question) == "STATE_PROVINCE" and not re.search(
                r"education|school|university|employer|company", question, re.IGNORECASE):
            value = str(getattr(self.profile, "state", "") or "").strip()
            if value:
                return value, "profile.state"
        if re.search(r"authorized to work in the u\.s\. for any employer", question, re.IGNORECASE):
            value = str(getattr(self.profile, "authorized_for_any_employer", "") or "").strip()
            if value:
                return value, "profile.authorized_for_any_employer"
        if re.search(r"sms|text message|texting|mobile message", question, re.IGNORECASE):
            preferred = str(getattr(self.profile, "preferred_contact_method", "") or "").strip().lower()
            if preferred:
                return ("Yes" if preferred in {"sms", "text", "text message", "phone"} else "No"), \
                    "profile.preferred_contact_method"
        if re.search(r"pre.employment background check", question, re.IGNORECASE):
            value = str(getattr(self.profile, "willing_to_submit_to_pre_employment_background_check", "") or "").strip()
            if value:
                return value, "profile.willing_to_submit_to_pre_employment_background_check"
        if re.search(r"bonus expectations", question, re.IGNORECASE):
            return str(getattr(self.profile, "bonus_expectations", "") or "").strip(), "profile.bonus_expectations"
        if re.search(r"willing and able to work.*office location.*3 days|minimum of 3 days per week", question,
                     re.IGNORECASE):
            return str(getattr(self.profile, "willing_to_work_onsite_three_days", "") or "").strip(), \
                "profile.willing_to_work_onsite_three_days"
        if re.search(r"relatives currently employed by occ", question, re.IGNORECASE):
            return str(getattr(self.profile, "relatives_employed_here", "") or "").strip(), \
                "profile.relatives_employed_here"
        if re.search(r"restrictive covenants|non.compete", question, re.IGNORECASE):
            return str(getattr(self.profile, "bound_by_non_compete", "") or "").strip(), \
                "profile.bound_by_non_compete"
        if re.search(r"\b(today'?s? date|application date|current date)\b", question, re.IGNORECASE):
            return date.today().isoformat(), "profile.application_date"
        if re.search(r"highest level of education|education achieved|educational background|degrees?", question,
                     re.IGNORECASE):
            education = getattr(self.profile, "education", ()) or ()
            if education:
                summary = "; ".join(
                    f"{level} in {field} from {school} ({year})"
                    for level, field, school, year in education
                )
                return summary, "profile.education"
        employer_slot = re.search(r"\bemployer\s+(\d+)\b", question, re.IGNORECASE)
        if employer_slot:
            index = int(employer_slot.group(1)) - 1
            current = getattr(self.profile, "current_employer", "")
            current_title = getattr(self.profile, "current_position_title", "")
            current_dates = getattr(self.profile, "current_employment_dates", "")
            current_location = getattr(self.profile, "current_employer_location", "")
            history = getattr(self.profile, "reasons_for_leaving", ()) or ()
            entries = [(current_title, current, current_dates, current_location)]
            entries.extend(("Network Engineer", employer, "", "") for employer, _reason in history
                           if employer != current)
            if 0 <= index < len(entries):
                title, employer, dates, location = entries[index]
                details = ", ".join(part for part in (title, employer, dates, location) if part)
                return details, "profile.work_history"
            return "", ""
        if re.search(r"(?:list|provide|enter).*work history|employment history|all work history|previous employment|"
                     r"other employment history", question, re.IGNORECASE):
            current = getattr(self.profile, "current_employer", "")
            current_title = getattr(self.profile, "current_position_title", "")
            current_dates = getattr(self.profile, "current_employment_dates", "")
            history = getattr(self.profile, "reasons_for_leaving", ()) or ()
            entries = [f"{current_title}, {current} ({current_dates})"] if current else []
            entries.extend(f"{employer} (reason for leaving: {reason})" for employer, reason in history
                           if employer != current)
            if entries:
                return "; ".join(entries), "profile.work_history"
        if re.search(r"employer|company|school|university|supervisor|reference|previous|this position|"
                     r"why (do|are) you", question, re.IGNORECASE):
            return "", ""
        explicit = self.library_answer(question)
        if explicit:
            return explicit, "profile.answer_library"
        if self.tracker is not None and hasattr(self.tracker, "recall_answer") \
                and not safety.is_legal_status_question(question):
            try:
                host = self._learning_host()
                for match in self.tracker.recall_answer(question, limit=3):
                    said = (match.get("answer") or "").strip()
                    if match.get("answered_by") == "user" and said \
                            and (not host or str(match.get("host") or "").lower() == host) \
                            and _same_question(match.get("question") or "", question) \
                            and (not control.options or any(_same_answer(said, option) for option in control.options)):
                        return said, "owner_earlier_answer"
            except Exception:
                pass
        # The answer bank: this question answered on any earlier application -- by the owner, or by the agent on one
        # the owner then sent -- on any portal (answer_bank.py). Last, so the profile and the owner's own answers
        # always come first; its answer goes into whatever this form draws through the same matcher as any other.
        banked = answer_bank.lookup(question, getattr(self.job, "company", "") or "")
        if banked and banked.get("value"):
            return str(banked["value"]), f"answer_bank.{banked.get('source', 'confirmed')}"
        return "", ""

    def answer_what_is_known(self, page, controls: list[Control],
                             required: set[str]) -> tuple[int, list[str]]:
        """Fill in every box whose answer is already known, with no help from
        anyone. Returns how many were filled and the questions still open.
        """
        filled, open_questions = 0, []
        handled_groups: set[str] = set()

        # Topological sorting: Country -> State -> City -> Others
        def _ctrl_prio(ctrl):
            # Country, then state, then city, then the rest: a form redraws what depends on them, and a ZIP or
            # street typed before the state is cleared when it is chosen (UKG, 30 September). Which box is which
            # comes from the one reading of questions, whatever the label's stars and spacing ("*State /
            # Province") -- hand-written patterns here missed UKG's and filled the state last.
            if getattr(self, "_entries", {}).get(ctrl.ref) is not None:
                return 3                     # a job's or a degree's place belongs to that entry, in page order
            concept = concept_matcher.match_concept(question=ctrl.question or "", container=ctrl.container,
                                                    context=ctrl.context, name=ctrl.name)
            return _RESIDENCE_ORDER.get(concept_matcher.confirm_concept(concept, ctrl.options), 3)

        sorted_controls = sorted(controls, key=_ctrl_prio)

        for control in sorted_controls:
            answerable_button = (control.role == "button" and control.GENERIC_NAMES.match(control.name.strip())
                                 and bool(control.container or control.group or control.context)) \
                or is_workday_choice_button(control)

            # Only empty boxes here. A box that already holds something the
            # profile contradicts is put right in correct_from_profile, which
            # checks who put the value there before changing it.
            if (control.role not in ANSWER_ROLES and not answerable_button) or control.disabled or control.answer:
                continue
            group_key = control.group if control.role in ("radio", "checkbox", "switch") else ""
            if group_key and group_key in handled_groups:
                continue
            if group_key and any(c.role == control.role and c.group == group_key and c.checked for c in controls):
                continue
            if group_key:
                handled_groups.add(group_key)
            if control.question in self.owner_answers:
                continue
            if not control.options and control.role in ("combobox", "listbox", "button"):
                control.options = published_choices(control.question, self._published_questions())
            value, source = self.known_answer(control)
            # A list that refused this very answer is not offered it again; a different answer -- after a fix or a
            # profile change -- is tried. Remembered by question alone, UKG's OPT question kept the "Graduated" that
            # failed at 13:48 and never took the "No" its reloaded code knew (30 September).
            if (control.question, value) in self._misfit:
                star = "*" if any(_same_question(control.question, q) for q in required) else ""
                open_questions.append(control.question + star)
                continue
            if not value and control.ref in getattr(self, "_entry_blank", ()):
                continue                     # the end date of the job the owner still has: blank is the answer
            if not value and self._profile_says_none(source):
                continue                     # the profile says there is none (no middle name): blank is the answer
            if not value:
                # Every box still empty is left open, not only the starred
                # ones: a form that marks nothing as required still has
                # questions on it that only a reader can answer.
                star = "*" if any(_same_question(control.question, q) for q in required) else ""
                open_questions.append(control.question + star)
                continue
            action = "fill" if control.role in ("textbox", "searchbox", "spinbutton") else "choose"
            # A radio group has one correct option.  ``choose`` selects that
            # option by its label; ``check`` would blindly tick whichever
            # radio happened to be visited first.
            # A tick box answered for itself ("checked": a consent the profile allows) is ticked itself, grouped or
            # not; a group's answer names its choices.
            # A tick box is ticked by a yes or no, or by its own words -- never by the fact of the question beside
            # it: UKG's "I decline to say" boxes took "Male", "Asian" and "I am not a veteran" and were ticked, and
            # a consent box took the phone number (30 September).
            if control.role in ("checkbox", "switch") and not control.group \
                    and value.strip().lower() not in _TICK_WORDS \
                    and option_match.best_option([control.name], value) is None:
                continue
            if control.role in ("checkbox", "switch") and (
                    not control.group or value.strip().lower() in ("checked", "true", "unchecked", "false")):
                action = "check" if value.strip().lower() not in ("no", "false", "0", "unchecked") else "uncheck"
            try:
                answer = Answer(control.ref, control.question, action, value, source)
                if self.do(page, answer, control):
                    filled += 1
                    self.written[control.question] = answer.value
                    self._remember(control, answer)
                    logger.info("KNEW: %s = %r (%s)", control.question[:44], value[:34], source)
                    # Quiescence check: if Country was updated, let network settle before filling dependent fields
                    if re.search(r"^\s*country(/region)?( of residence)?\s*:?\s*\*?\s*$", control.question or "", re.I):
                        logger.info("LOCATION_SWEEP: Country answered; waiting for network quiescence...")
                        try:
                            self.tab(page).wait_for_load_state("networkidle", timeout=7_000)
                        except Exception:
                            pass
                        self.settle(page, 1_000)
                else:
                    star = "*" if any(_same_question(control.question, q) for q in required) else ""
                    open_questions.append(control.question + star)
                    self.failed.append(f"{control.question[:60]} = {value[:40]!r}")
                    if action == "choose":
                        self._misfit.add((control.question, value))
            except Exception as exc:
                star = "*" if any(_same_question(control.question, q) for q in required) else ""
                open_questions.append(control.question + star)
                self.failed.append(f"{control.question[:60]} = {value[:40]!r}")
                logger.info("Could not answer %r automatically: %s", control.question[:60],
                            str(exc).splitlines()[0][:100])
        return filled, open_questions


    def stuck_on_errors(self, page) -> str:
        """After a press that did not move the page on: what its own errors say to do.

        The first time, the errors are acted on -- an error about a file makes the agent attach it again (it
        believed it attached) -- and passed to the planner as feedback. The same errors after the next press
        mean pressing again will not help: "stop:" and the page's own words, for the owner."""
        try:
            errors = page_errors(self.snapshot(page))
        except Exception:
            return ""
        if not errors:
            return ""
        key = tuple(sorted(errors))
        if key == getattr(self, "_last_press_errors", ()):
            return "stop:the form will not go on; it says: " + "; ".join(errors[:4])
        self._last_press_errors = key
        if any(_UPLOAD_ERROR.search(e) for e in errors):
            logger.info("The form says a file is missing -- attaching it again")
            self.resume_uploaded = False
            self._letter_attached = False
        logger.info("THE FORM SAYS: %s", "; ".join(errors[:4])[:200])
        return "the form says: " + "; ".join(errors[:4])

    def carry_on_without_the_ai(self, controls: list[Control], required: set[str], still_open: list[str],
                                snapshot: str, why: str) -> Optional["PagePlan"]:
        """The page plan when the AI cannot be asked, or None when the page needs it.

        Blue Cross and Blue Shield of Louisiana (29 September): My Experience needed only the resume, which the
        agent attaches itself; the questions left open were optional (Skills, Phone Extension). The AI's daily
        allowance was spent, and the run stopped although nothing on the page needed an answer. Now, with every
        required question answered and a plain way forward, the optional ones are left blank and the run goes on;
        a required question still open stops it, as before."""
        self.required_left = []
        required_open = [q for q in still_open
                         if q.rstrip().endswith("*") or any(_same_question(q.rstrip(" *"), r) for r in required)]
        # Workday names a required list "<question> Select One Required": the star it draws is on a line of its
        # own, so the question was taken for optional and Next was pressed into the same error three times
        # (Rackspace's AI-screening consent, 29 September).
        required_open += [c.question for c in controls
                          if c.role in ("button", "combobox", "listbox") and not c.disabled and not c.answer
                          and re.search(r"\brequired\s*$", c.name or "", re.IGNORECASE)
                          and c.question not in required_open]
        errors = page_errors(snapshot)
        if required_open or errors:
            self.required_left = required_open or errors
            return None
        plan = self.profile_plan(controls, required, [], snapshot)
        if not plan.next_ref or _blank_choice_on_page([c for c in controls if "*" in (c.question or "")]):
            return None
        logger.info("NO AI (%s): only optional questions are left (%s) -- leaving them blank and going on",
                    why.splitlines()[0][:80] if why else "unavailable", "; ".join(q[:40] for q in still_open[:4]) or "none")
        if still_open:
            note = "left blank without the AI (optional): " + "; ".join(q[:50] for q in still_open[:6])
            if note not in self.notes:
                self.notes.append(note)
        return plan

    def _past_employers(self) -> list[str]:
        """Who the person has worked for, from the profile and the resume (read once per run)."""
        if getattr(self, "_employers_cache", None) is None:
            self._employers_cache = employment_history.past_employers(
                self.profile, getattr(self.resume, "raw_text", "") or "")
        return self._employers_cache

    def _published_questions(self) -> list:
        analysis = getattr(self.job, "analysis", None)
        return list((analysis or {}).get("published_questions") or []) if isinstance(analysis, dict) else []

    def facts(self, controls: list[Control]) -> dict:
        profile = asdict(self.profile) if hasattr(self.profile, "__dataclass_fields__") else dict(vars(self.profile))
        phone, code = profile.get("phone", ""), profile.get("phone_country_code", "")
        earlier: list[dict] = []
        seen: set[str] = set()
        if self.tracker is not None and hasattr(self.tracker, "recall_answer"):
            for c in controls:
                q = c.question
                if not q or q in seen or c.role not in ANSWER_ROLES or c.answer:
                    continue
                seen.add(q)
                if safety.is_legal_status_question(q) or safety.is_attestation(q):
                    continue   # never replayed: the owner answers these on each form
                try:
                    for match in self.tracker.recall_answer(q, limit=3):
                        if match.get("answered_by") == "user" and (match.get("answer") or "").strip():
                            earlier.append({"question": match.get("question"), "answer": match.get("answer")})
                            break
                except Exception:
                    continue
        return {
            "today": date.today().isoformat(),
            "job": {"title": getattr(self.job, "title", ""), "company": getattr(self.job, "company", ""),
                    # The posting itself, so an answer fits what the employer asks for (the owner's request,
                    # 29 September); the planner is told it describes the job, not the applicant.
                    "description": (getattr(self.job, "raw_text", "") or "")[:JOB_POSTING_CHARS]},
            "profile": profile,
            "phone_with_country_code": f"{code} {phone}".strip() if code and not phone.startswith("+") else phone,
            "resume_text": (getattr(self.resume, "raw_text", "") or "")[:12_000],
            "owner_earlier_answers": earlier[:40],
            "questions_the_site_publishes": self._published_questions()[:60],
            "documents": {"resume": self.resume_file.name if self.resume_file else "",
                          "cover_letter": "can be written for this job" if self.cover_letter else ""},
        }

    # -- one page -----------------------------------------------------------------
    def run(self, page) -> Outcome:
        """Works through the application until it is submitted or needs the owner."""
        self._ensure_state()
        tries = 0
        stick_tries = 0      # re-reads of one page because an answer did not stay
        last_fingerprint = ""
        feedback = ""
        for _ in range(MAX_PAGES * MAX_TRIES_PER_PAGE):
            tab = self.tab(page)
            if tab.is_closed():
                return Outcome("gave_up", page, ["the browser window was closed"])
            self._current_host = urlparse(getattr(tab, "url", "")).netloc.lower()
            if not self._resume_autofill_attempted and self.resume_file \
                    and hasattr(self.assistant, "autofill_from_resume"):
                try:
                    autofilled = self.assistant.autofill_from_resume(tab, self.resume_file)
                    if autofilled:
                        self._resume_autofill_attempted = True
                        logger.info("AUTOFILL: resume parser was run before reading the application page")
                        try:
                            wipe_and_enforce_location_sweep(tab, profile=self.profile)
                        except Exception as exc:
                            logger.debug("Sweep after resume autofill failed: %s", exc)
                        self.settle(page, 2_000)
                        continue
                    if not getattr(self.assistant, "on_job_description", lambda _page: False)(tab):
                        self._resume_autofill_attempted = True
                except Exception as exc:
                    self._resume_autofill_attempted = True
                    logger.info("AUTOFILL: resume parser could not run: %s", str(exc).splitlines()[0][:120])
            try:
                self.assistant.dismiss_cookie_banner(tab)
            except Exception:
                pass
            if safety.captcha_visible(page):
                return Outcome("captcha", page, ["a CAPTCHA is showing -- only you can complete it"])
            if getattr(self.profile, "requires_visa_sponsorship", False):
                disqualified, clause, shot = safety.check_visa_sponsorship_shield(
                    tab, self.profile, job_dir=self.job_dir
                )
                if disqualified:
                    logger.warning("VISA SPONSORSHIP SHIELD: Employer non-sponsorship declaration found: %s", clause)
                    return Outcome("disqualified_policy_mismatch", page, [clause])

            snapshot = self.read_when_loaded(page)
            fingerprint = re.sub(r"\[ref=[\w-]+\]|\[active\]", "", snapshot)
            if fingerprint == last_fingerprint:
                tries += 1
                if tries >= MAX_TRIES_PER_PAGE:
                    return Outcome("owner_needed", page, ["the page did not move on after several tries"
                                                          + (f": {feedback}" if feedback else "")])
            else:
                tries, last_fingerprint = 0, fingerprint
            self.pages_read += 1
            if self.pages_read > MAX_PAGES * 2:
                return Outcome("gave_up", page, ["too many pages in one run"])

            controls = parse_snapshot(snapshot)
            self._save(snapshot)
            shape = tuple(sorted({c.question for c in controls if c.question}))
            self._shapes[hash(shape)] = self._shapes.get(hash(shape), 0) + 1
            if self._shapes[hash(shape)] > MAX_READS_PER_PAGE:
                return Outcome("owner_needed", page, ["this page keeps asking the same things and is not "
                                                      "moving on -- the rest of it needs you"])
            # A confirmation is read before anything else on the page: Workday's says "We've Received Your
            # Application!" over a form to make a Candidate Home account, and was taken for a sign-in page --
            # the application went unrecorded and the run went on asking about an account (Aristocrat, 29 Sept).
            if self._site_confirms(tab):
                if self.final_pressed:
                    return Outcome("submitted", page, ["the site confirmed the application"])
                return Outcome("owner_needed", page, ["the site says this application was already sent"])
            if self.sign_in_step(page, controls):
                self.settle(page)
                continue
            if self.account_blocker:
                # Only the owner can do this (a verification link, a locked account, a refused password):
                # say so, rather than read the sign-in page as a form to answer.
                return Outcome("owner_needed", page, [self.account_blocker])
            if self.complete_account_code(page, controls, snapshot):
                self.settle(page)
                continue
            if self.open_entry_for_missing_field(page, snapshot, controls):
                self.settle(page, 1_500)
                continue
            if self.add_entries_the_site_way(page, snapshot):
                self.settle(page, 1_500)
                continue
            if self.wake_loading_control(page, snapshot):
                self.settle(page, 2_000)
                continue
            if hasattr(self.assistant, "accept_consent_dialog") and getattr(self.profile, "accept_application_privacy_prompts", True):
                try:
                    if self.assistant.accept_consent_dialog(tab):
                        self.settle(page, 1_500)
                        continue
                except Exception as exc:
                    logger.debug("Consent dialog check: %s", exc)
            # What the owner has already settled is filled in here, by the agent
            # itself: his own details and the answers he has given before.
            # Answering can enable dependent fields (e.g. Country -> State/Province).
            # Loop while newly enabled known fields continue to be filled.
            for _pass in range(3):
                required = required_questions(snapshot)
                knew, still_open = self.answer_what_is_known(page, controls, required)
                if not knew:
                    break
                self.settle(page, 800)
                snapshot = self.read_when_loaded(page)
                controls = parse_snapshot(snapshot)
                still_open = [q for q in still_open
                              if not any(_same_question(c.question, q) and c.answer for c in controls)]
                self._commit_learned_memory(controls)
            # "Select one or more locations where you'd like to apply": the owner's rule (location_choice.py).
            if self.answer_location_choices(page, controls):
                self.settle(page, 600)
                snapshot = self.read_when_loaded(page)
                controls = parse_snapshot(snapshot)
            # Fields the accessibility snapshot does not show as questions -- a hidden resume input, a list drawn as a
            # button -- found by what they are (form_fields.py).
            if self.inventory_pass(page):
                self.settle(page, 600)
                snapshot = self.read_when_loaded(page)
                controls = parse_snapshot(snapshot)
                still_open = [q for q in still_open
                              if not any(_same_question(c.question, q) and c.answer for c in controls)]
            plan = None
            if self.uses_profile_planner():
                # Autonomous mode is intentionally deterministic.  It never
                # hands an application page, screening answer, or navigation
                # choice to Claude/the session; unknown *required* questions
                # are returned as a clear profile-data gap instead.
                plan = self.profile_plan(controls, required, still_open, snapshot)
            if plan is None:
                plan = self.posting_plan(controls, required, still_open, snapshot)
            if plan is None:
                # 1. Profile-first: if the profile already answered everything on
                # this page (still_open is empty), build the plan from the
                # profile without spending an AI API call.
                if not still_open:
                    plan = self.profile_plan(controls, required, still_open, snapshot)
                    if (plan.next_ref or plan.for_owner) and not _blank_choice_on_page(controls):
                        logger.info("PAGE_FULLY_KNOWN: all fields answered from profile -- skipping AI call")
                    else:
                        # Nothing left to answer, but no plain Next either: a work-history
                        # page that only offers "Add Experience" needs the plan to go on.
                        plan = None
                else:
                    # 2. Targeted Per-Question AI Answering: For remaining unanswered questions,
                    # make lightweight per-question calls with candidate resume context (< 300 tokens each).
                    self.follow_the_chosen_brain()
                    if hasattr(self.claude, "answer_single_question"):
                        resume_text = getattr(self, "_resume_text_cache", None)
                        if resume_text is None:
                            try:
                                if self.resume_file and hasattr(self.assistant, "extract_resume_text"):
                                    resume_text = self.assistant.extract_resume_text(self.resume_file)
                                elif hasattr(self.job, "resume_text"):
                                    resume_text = getattr(self.job, "resume_text", "")
                            except Exception:
                                resume_text = ""
                            self._resume_text_cache = resume_text or ""

                        newly_answered = 0
                        for ctrl in list(controls):
                            if not ctrl.question or ctrl.answer or ctrl.disabled \
                                    or ctrl.ref in getattr(self, "_entry_blank", ()):
                                continue
                            if not any(_same_question(ctrl.question, q) for q in still_open):
                                continue
                            if safety.is_attestation(ctrl.question) or safety.is_legal_status_question(ctrl.question):
                                continue
                            if concept_matcher.is_self_identification(
                                    f"{ctrl.question} {ctrl.container} {ctrl.context}"):
                                continue            # who the owner is: never the AI's to answer
                            if self._optional_entry_box(ctrl, still_open):
                                continue            # an optional box of a job or degree: left blank, not written up
                            if self._profile_says_none(self.known_answer(ctrl)[1]):
                                continue            # the profile says there is none: never the AI's to fill
                            if is_secret_box(ctrl.question) or is_honeypot(ctrl.question):
                                continue            # a password or a robots' decoy is never the AI's to answer
                            if ctrl.role in ("textbox", "searchbox") \
                                    and form_fields.tags_beside(self.locate(page, ctrl.ref)):
                                continue            # a tag box that already holds tags (UKG's Skills) is answered
                            try:
                                # A text box tells how long an answer it takes and what it
                                # asks beside the label ("max 150 words", "0/500").
                                box = open_answers.read_box(self.locate(page, ctrl.ref)) \
                                    if ctrl.role in ("textbox", "searchbox") else {}
                                # A box inside a job or degree entry is asked about with that entry named: "Country
                                # of Institution" alone got one answer (India) for both degrees (Steelcase).
                                entry = getattr(self, "_entries", {}).get(ctrl.ref)
                                about = repeated_entries.describe(entry, getattr(self, "history", {}) or {}) \
                                    if entry is not None else ""
                                # A list is asked about with its choices: one that draws them only when opened
                                # is opened and read first, or the AI writes an essay for a pick-one question.
                                if not ctrl.options and ctrl.role in ("combobox", "listbox"):
                                    field = self._inventory_field(page, ctrl)
                                    if field is not None:
                                        ctrl.options = form_fields.read_choices(field)
                                val = self.claude.answer_single_question(
                                    question=f"{ctrl.question} (for: {about})" if about else ctrl.question,
                                    options=ctrl.options,
                                    resume_text=self._resume_text_cache,
                                    profile=self.profile,
                                    job_title=getattr(self.job, "title", ""),
                                    company=getattr(self.job, "company", ""),
                                    job_text=getattr(self.job, "raw_text", "") or "",
                                    box=box,
                                )
                                if val:
                                    action = "fill" if ctrl.role in ("textbox", "searchbox", "spinbutton") else "choose"
                                    ans = Answer(ctrl.ref, ctrl.question, action, val, "ai_single_question")
                                    if self.do(page, ans, ctrl):
                                        newly_answered += 1
                                        self.written[ctrl.question] = val
                                        self._remember(ctrl, ans)
                                        logger.info("AI_ANSWERED_QUESTION: %r = %r", ctrl.question[:50], val[:50])
                                        if not ctrl.options and open_answers.is_open(ctrl.question,
                                                                                     box.get("multiline", False)):
                                            # Written in the owner's name: the hand-over says to read it.
                                            written = getattr(self.assistant, "written_answers", None)
                                            if written is None:
                                                written = self.assistant.written_answers = []
                                            if ctrl.question not in written:
                                                written.append(ctrl.question)
                            except Exception as exc:
                                # Said out loud: at debug level a broken call (a missing function)
                                # left every question blank with no word in the run's log.
                                logger.warning("Could not answer %r with the AI: %s: %s", ctrl.question[:60],
                                               type(exc).__name__, str(exc).splitlines()[0][:120] if str(exc) else "")

                        if newly_answered > 0:
                            self.settle(page, 800)
                            snapshot = self.read_when_loaded(page)
                            controls = parse_snapshot(snapshot)
                            still_open = [q for q in still_open
                                          if not any(_same_question(c.question, q) and c.answer for c in controls)]
                            self._commit_learned_memory(controls)

                    if not still_open:
                        plan = self.profile_plan(controls, required, still_open, snapshot)
                        if (plan.next_ref or plan.for_owner) and not _blank_choice_on_page(controls):
                            logger.info("PAGE_FULLY_KNOWN: all fields answered after targeted AI -- "
                                        "skipping full-page AI call")
                        else:
                            plan = None

                if plan is None:
                    # 3. Full-page plan fallback (if still_open couldn't be resolved or complex multi-step navigation needed)
                    self.follow_the_chosen_brain()
                    if still_open:
                        feedback = ((feedback + " | ") if feedback else "") + \
                            "the agent could not answer these itself: " + "; ".join(still_open[:12])
                    # A dropdown that draws its list only when opened is read by opening
                    # it, so Claude is told the choices instead of guessing at them.
                    hidden = self.read_hidden_choices(page, controls, snapshot, only=still_open)
                    asked = feedback
                    if hidden:
                        snapshot = self.read_when_loaded(page)
                        controls = parse_snapshot(snapshot)
                        asked = ((feedback + " | ") if feedback else "") + hidden
                    try:
                        plan = PagePlan.from_json(self.claude.plan_page(compact_snapshot(snapshot),
                                                                        self.facts(controls), asked))
                    except Exception as exc:
                        message = str(exc)
                        plan = self.carry_on_without_the_ai(controls, required, still_open, snapshot, message)
                        if plan is None and self.required_left:
                            return Outcome("owner_needed", page, [
                                "needs your answer (the AI is unavailable): " + q[:160] for q in self.required_left[:4]])
                        if plan is None:
                            if "out of credit" in message or "credit balance" in message.lower():
                                return Outcome("owner_needed", page, [message.split(": ", 1)[-1][:200]])
                            return Outcome("owner_needed", page,
                                           [f"could not read this page ({message.splitlines()[0][:120]})"])
            logger.info("READ: %s%s -- %d to answer, %d for you, next: %s %r", plan.page_kind.replace("_", " "),
                        f" ({plan.step})" if plan.step else "", len(plan.answers), len(plan.for_owner),
                        plan.next_kind.replace("_", " "), plan.next_label[:40])

            if ASKS_FOR_A_REFRESH.search(snapshot) and not self._refreshed:
                # Do what the page asks, once, keeping everything already saved.
                self._refreshed = True
                logger.info("The page says something went wrong and asks to be refreshed -- refreshing it")
                try:
                    tab.reload(wait_until="domcontentloaded", timeout=60_000)
                except Exception as exc:
                    logger.debug("Could not refresh: %s", str(exc).splitlines()[0][:70])
                self.settle(page, 3_000)
                continue

            said = ACCOUNT_ERROR.search(snapshot)
            if said or (plan.page_kind == "error" and not controls):
                message = " ".join((said.group(0) if said else "the site showed an error").split())
                if self._google_tried - self._google_failed:
                    # The site will not take the Google account (the error may
                    # show on Google's own address, so every site just tried
                    # with it is marked): use the site's own sign-in from now
                    # on, and that is worth another go at the application.
                    self._google_failed |= set(self._google_tried)
                    self.assistant.google_refused_on = set(self._google_failed)
                    self._retried_after_error = False
                if not self._retried_after_error:
                    # The Google account the site holds cannot be used (NVIDIA:
                    # "Account is Inactive"). Start again from the posting and
                    # take the site's own sign-in, which Google is now past.
                    self._retried_after_error = True
                    logger.info("The site says %r after signing in -- starting again from the posting "
                                "and using its own sign-in", message[:60])
                    tab.goto(getattr(self.job, "url", "") or tab.url, wait_until="domcontentloaded", timeout=60_000)
                    self.settle(page)
                    continue
                return Outcome("owner_needed", page, [f"the site says: {message[:120]} -- the account it holds "
                                                      f"for you cannot be used, so it needs you"])
            if plan.page_kind == "captcha":
                return Outcome("captcha", page, ["a CAPTCHA is showing -- only you can complete it"])
            if (plan.page_kind == "confirmation" or self._site_confirms(tab)) and self._site_confirms(tab):
                if self.final_pressed:
                    return Outcome("submitted", page, ["the site confirmed the application"])
                return Outcome("owner_needed", page, ["the site says this application was already sent"])

            given = (self.correct_from_profile(page, plan, controls)
                     + self.apply_answers(page, plan, controls)
                     + self.attach_documents(page, snapshot, controls))

            # Read back what is on the page now, whoever filled it, and check
            # every answer the agent gave is really there.
            self.settle(page, 800)
            after = parse_snapshot(self.snapshot(page))
            self._commit_learned_memory(after)
            # Pressing Next can land on a brand-new page (e.g. a sponsorship /
            # work-authorization questionnaire) that this cycle's plan and
            # controls never saw. Give it the same profile-correction pass
            # before blockers() below judges it, so a legal-status question the
            # profile already answers gets filled here rather than stopping
            # the run to ask the owner something the agent already knows.
            just_arrived = self.correct_from_profile(page, plan, after)
            if just_arrived:
                given += just_arrived
                self.settle(page, 500)
                after = parse_snapshot(self.snapshot(page))
                self._commit_learned_memory(after)
            if given:
                # Something was filled in, so pressing "Add" again is progress
                # (the next job), not the loop the count is there to stop.
                self._pressed.clear()
            missing = self.not_stuck(given, after) + list(getattr(self, "failed", []))
            if missing:
                stick_tries += 1
                if stick_tries < MAX_TRIES_PER_PAGE:
                    feedback = "these answers did not stay on the page, try another way: " + "; ".join(missing)
                    logger.info("NOT STUCK: %s -- reading the page again", "; ".join(missing)[:200])
                    continue
                return Outcome("owner_needed", page, [f"could not set: {m}" for m in missing])
            # Only the last step must be complete before the agent presses on.
            about_to_send = plan.next_kind in ("final_submit", "none") or safety.is_submit_label(plan.next_label)
            blockers = self.blockers(page, plan, after, about_to_send=about_to_send)
            if about_to_send:
                required = required_questions(self.snapshot(page))
                given_questions = set(self.written) | {answer.question for answer, _control in given}
                missing_required = []
                for control in after:
                    question = control.question
                    # A star that marks a question stands at its start or end ("Email address *", "* Company"),
                    # not inside it: UKG's password rule "Special characters (e.g. !@#$%^&*)" was named a blank
                    # required field (30 September).
                    marked_required = bool(_REQUIRED_STAR.search(question or "")) \
                        or any(_same_question(question, item) for item in required)
                    # A "question" with no words is layout, not a question: UKG drew a row of required stars
                    # ("* * * * *") and the run stopped for it as a blank required field (30 September).
                    if not question or not marked_required or not re.search(r"[A-Za-z]{2}", question):
                        continue
                    answered = bool(control.answer)
                    if control.role in ("radio", "checkbox", "switch") and control.group:
                        answered = any(other.role == control.role and other.group == control.group and other.checked
                                       for other in after)
                    if control.role in ("radio", "checkbox", "switch"):
                        answered = answered or any(other.role == control.role
                                                  and _same_question(other.question, question)
                                                  and other.checked for other in after)
                    if control.role == "button" and re.search(r"resume|cv|curriculum|select files?", question,
                                                               re.IGNORECASE):
                        # Resume attachment has one authority: submit_gate, whose
                        # check is richer than this one (earlier-run uploads count).
                        continue
                    if not answered and any(_same_question(question, item) for item in given_questions):
                        answered = True
                    if not answered and control.role in ("textbox", "searchbox") \
                            and form_fields.tags_beside(self.locate(page, control.ref)):
                        answered = True         # a tag box holding tags (UKG's Skills): its text box stays empty
                    if not answered and question not in missing_required:
                        missing_required.append(question)
                # Whatever the page draws it as: a required field a person can see, still empty, is named -- the
                # snapshot alone missed BambooHR's State and reported the page complete (Secunetics).
                try:
                    for blank in form_fields.blank_required(form_fields.inventory(self.tab(page))):
                        question = re.sub(r"[\s*✱:]+$", "", blank.question or blank.label)
                        if question and re.search(r"[A-Za-z]{2}", question) \
                                and not any(_same_question(question, m) for m in missing_required) \
                                and not any(_same_question(question, g) for g in given_questions):
                            missing_required.append(question)
                except Exception as exc:
                    logger.debug("Field inventory at the last step failed: %s", exc)
                blockers += [f"required field is still blank: {question}" for question in missing_required]
            if blockers:
                return Outcome("owner_needed", page, blockers)

            rejected = self.rejected_by_the_page(self.snapshot(page), after)
            if rejected:
                feedback = ("the page still says these are empty although they show a value, so type nothing: "
                            "open each one's list and choose from it -- " + "; ".join(rejected))
                logger.info("REFUSED BY THE PAGE: %s", "; ".join(rejected)[:160])
            moved, page, feedback = self.press_next(page, plan, after) if not rejected else ("retry", page, feedback)
            if moved == "retry":
                stuck = self.stuck_on_errors(page)
                if stuck.startswith("stop:"):
                    return Outcome("owner_needed", page, [stuck[5:]])
                if stuck:
                    feedback = stuck
            if moved == "moved":
                self._last_press_errors = ()
                stick_tries = 0
            if moved == "submitted":
                return Outcome("submitted", page, ["the site confirmed the application"])
            if moved == "stop":
                return Outcome("owner_needed", page, [feedback])
        return Outcome("gave_up", page, ["too many pages in one run"])

    # -- answering ------------------------------------------------------------------
    def apply_answers(self, page, plan: PagePlan, controls: list[Control]) -> list[tuple[Answer, Control]]:
        """Gives the plan's answers the rules allow; returns the ones given.
        The ones that could not be given are kept in self.failed.
        Implements Task 3.2: Dependent Cascading Fields (Topological Re-Scan)."""
        given: list[tuple[Answer, Control]] = []
        self.failed = []
        current_controls = list(controls)
        by_ref = {c.ref: c for c in current_controls}
        signing = [a for a in plan.answers if self._is_signature(a, by_ref.get(a.ref))]
        queue: list[Answer] = _without_contradicting_none(
            [a for a in plan.answers if a not in signing], by_ref)
        processed_refs: set[str] = set()

        while queue:
            answer = queue.pop(0)
            if answer.ref in processed_refs:
                continue
            control = by_ref.get(answer.ref)
            if control is None:
                logger.info("SKIPPED: %r is not on the page", answer.question[:60])
                continue
            # A box inside a job or degree entry takes that entry's record, whatever the page plan proposed; the
            # plan's value stands only where the record holds nothing (a school's country, say).
            entry = getattr(self, "_entries", {}).get(control.ref)
            if entry is not None and answer.action in ("fill", "choose") \
                    and not str(answer.source or "").startswith("work history"):
                value, source, blank = repeated_entries.answer(entry, getattr(self, "history", {}) or {})
                if blank:
                    logger.info("ENTRY: %r stays blank -- %s is the current job (the plan said %r)",
                                entry.label, source, answer.value[:40])
                    processed_refs.add(control.ref)
                    continue
                if value and not _same_answer(answer.value, value):
                    logger.info("ENTRY: %r takes %r from %s, not the plan's %r", entry.label, value[:40], source,
                                answer.value[:40])
                    answer = Answer(answer.ref, answer.question, answer.action, value, source)
            refusal = self.refusal(page, answer, control, current_controls)
            if refusal:
                known_val, _ = self.known_answer(control)
                if (known_val and _same_answer(control.answer, known_val) and _same_answer(answer.value, known_val)) or \
                   ("already answered from your profile" in refusal and control.role == "radio"):
                    logger.info("PRESERVED FROM PROFILE: %r = %r (ignored planner's %r)",
                                control.question or answer.question, control.answer or "checked", answer.value)
                    processed_refs.add(control.ref)
                    continue
                logger.info("LEFT FOR YOU: %r -- %s", (control.question or answer.question)[:70], refusal)
                plan.for_owner.append({"question": control.question or answer.question, "reason": refusal,
                                       "required": "*" in (control.question or "")})
                if control.role != "radio" and "already answered" in refusal \
                    and control.answer and not _same_answer(control.answer, answer.value):
                    self.failed.append(f"{control.question or answer.question} = {answer.value[:40]!r}")
                processed_refs.add(control.ref)
                continue
            try:
                done = self.do(page, answer, control)
            except Exception as exc:
                done = False
                logger.info("Could not answer %r: %s", answer.question[:60], str(exc).splitlines()[0][:100])
            if not done:
                question = (control.question or answer.question)[:70]
                self.failed.append(f"{question} = {answer.value[:40]!r}")
                if "*" not in (control.question or "") and "*" not in (control.name or ""):
                    note = f"{question}: could not choose {answer.value[:40]!r} -- this form doesn't offer it"
                    if note not in self.notes:
                        self.notes.append(note)
                    logger.info("LEFT BLANK: %s", note)
            if done:
                # An owner-approved alternative was chosen in its place: the answer is what is on the page,
                # or the check that it stayed would call it a different answer and re-read the page for ever.
                instead = self._chosen_instead.pop(control.ref, None)
                if instead:
                    answer.value = instead
                given.append((answer, control))
                self.written[control.question or answer.question] = answer.value
                self.assistant.values.record(self.tab(page), f"aria:{control.question}", answer.value,
                                             answer.source or "agent")
                logger.info("ANSWERED: %r -> %r (from %s)", (control.question or answer.question)[:60],
                            answer.value[:50], answer.source or "?")
                self._remember(control, answer)

            processed_refs.add(control.ref)

            # Task 3.2: Dependent Cascading Fields (Topological Re-Scan)
            # After interacting with any dropdown, checkbox, or radio trigger:
            is_trigger = control.role in ("combobox", "listbox", "radio", "checkbox", "switch") or answer.action in ("choose", "check")
            if done and is_trigger:
                try:
                    # 1. 500ms debounce
                    self.tab(page).wait_for_timeout(500)

                    # 2. Re-scan DOM subtree / snapshot
                    new_snapshot = self.snapshot(page)
                    new_controls = parse_snapshot(new_snapshot)
                    newly_mounted: list[Control] = []

                    for nc in new_controls:
                        if nc.ref not in by_ref:
                            by_ref[nc.ref] = nc
                            current_controls.append(nc)
                            newly_mounted.append(nc)

                    if newly_mounted:
                        logger.info("CASCADE RE-SCAN: detected %d newly mounted field(s) after %r",
                                    len(newly_mounted), (control.question or answer.question)[:50])
                        # Prepend newly mounted required or conditional fields to the front of active input filling queue
                        cascading_answers: list[Answer] = []
                        for nc in newly_mounted:
                            if nc.role in ANSWER_ROLES and not nc.disabled and nc.ref not in processed_refs:
                                val, src = self.known_answer(nc)
                                if not val:
                                    val, src = self._attestation_answer(nc)
                                action = "choose" if nc.role in ("combobox", "listbox", "radio") else (
                                    "check" if nc.role in ("checkbox", "switch") else "fill"
                                )
                                cascading_answers.append(Answer(
                                    ref=nc.ref,
                                    question=nc.question or nc.name,
                                    action=action,
                                    value=val,
                                    source=src or "cascading_field",
                                ))
                        if cascading_answers:
                            queue = cascading_answers + queue
                            logger.info("CASCADE RE-SCAN: prepended %d answerable cascading field(s) to front of queue",
                                        len(cascading_answers))
                except Exception as exc:
                    logger.debug("Cascade re-scan error: %s", exc)

        if signing:
            given += self.sign(page, plan, signing, given)
        return given

    @staticmethod
    def _is_signature(answer: Answer, control: Optional[Control]) -> bool:
        texts = [answer.question] + ([control.question, control.name] if control else [])
        return any(safety.is_attestation(t) and not safety.is_privacy_consent(t) for t in texts if t)

    def sign(self, page, plan: PagePlan, signing: list[Answer],
             given_here: list[tuple[Answer, Control]] = ()) -> list[tuple[Answer, Control]]:
        """Signs the page's declarations on the owner's behalf -- last, and only
        when everything else on the page is right."""
        def leave(reason: str) -> list:
            for answer in signing:
                logger.info("NOT SIGNED: %r -- %s", answer.question[:70], reason)
                plan.for_owner.append({"question": answer.question, "reason": reason, "required": True})
            return []

        if not getattr(self.profile, "sign_attestations", False):
            return leave("a declaration or signature -- you haven't allowed the agent to give it")
        self.settle(page, 800)
        now = parse_snapshot(self.snapshot(page))
        # Every answer given on this page must be showing before anything is signed.
        problems = list(getattr(self, "failed", [])) + self.not_stuck(list(given_here), now)
        problems += safety.legal_answer_conflicts(answered_fields(now), self.profile)
        for item in plan.for_owner:
            question = str(item.get("question") or "")
            if (bool(item.get("required")) or "*" in question) and not any(
                    _same_question(c.question, question) and c.answer for c in now):
                problems.append(f"{question[:60]} is still unanswered")
        if problems:
            return leave("not signed while something on the page is wrong or missing: " + "; ".join(problems)[:200])

        given: list[tuple[Answer, Control]] = []
        by_ref = {c.ref: c for c in now}
        for answer in signing:
            control = by_ref.get(answer.ref)
            if control is None:
                continue
            refusal = self.refusal(page, answer, control, now)
            if refusal:
                logger.info("NOT SIGNED: %r -- %s", control.question[:70], refusal)
                plan.for_owner.append({"question": control.question, "reason": refusal, "required": True})
                continue
            try:
                done = self.do(page, answer, control)
            except Exception as exc:
                done = False
                logger.info("Could not sign %r: %s", control.question[:60], str(exc).splitlines()[0][:100])
            if not done:
                self.failed.append(f"{control.question[:60]} = {answer.value[:40]!r}")
                continue
            given.append((answer, control))
            self.written[control.question] = answer.value
            note = f"signed {control.question[:80]!r} on your behalf (your decision of 2026-09-17)"
            self.notes.append(note)
            logger.info("SIGNED: %s", note)
        return given

    def _attach_resume_to_its_input(self, tab, fields=None) -> bool:
        """The resume put straight into the page's own file input, found by the section it sits in -- no button, no
        file window. Jobvite (Altamira, 30 September): its 'Select' button opens a Dropbox / File / LinkedIn menu, the
        agent waited for a file window that never came, and the menu left open over the page stopped the run -- the
        page's hidden file input takes the file directly."""
        if not self.resume_file or self.resume_uploaded:
            return False
        try:
            fields = fields if fields is not None else form_fields.inventory(tab)
        except Exception as exc:
            logger.debug("Field inventory failed: %s", exc)
            return False
        target = form_fields.resume_input(fields)
        here = (urlparse(getattr(tab, "url", "")).path, target.name or target.label) if target else None
        if target is None or target.value or here in self._attached_here:
            return False
        if not form_fields.attach_resume(tab, self.resume_file, fields):
            return False
        self._attached_here.add(here)
        self._resume_went_on(self.resume_file)
        self.resume_seen = True
        for asked in {target.question, target.label, target.section} - {""}:
            self.written[asked] = Path(self.resume_file).name   # the page's own words for it: answered
        where = target.section or target.question or target.label or target.name
        logger.info("ATTACHED: the resume (%s) -- its input found by where it sits: %r",
                    Path(self.resume_file).name, where[:60])
        return True

    def inventory_pass(self, page) -> list[str]:
        """What the field inventory finds that the snapshot does not: the resume's file input (told apart by the
        section it sits in -- 'Attach', 'Choose File*', 'From Device' say nothing), and lists a page draws as a
        button ('State –Select–'). Returns what was done."""
        tab = self.tab(page)
        try:
            fields = form_fields.inventory(tab)
        except Exception as exc:
            logger.debug("Field inventory failed: %s", exc)
            return []
        done: list[str] = []
        if self._attach_resume_to_its_input(tab, fields):
            done.append("resume")
        try:
            controls = parse_snapshot(self.snapshot(page))
        except Exception:
            controls = []
        # A label that appears on several boxes belongs to repeated entries (a job's 'End date'): those are the
        # entry-aware path's (repeated_entries.py), never answered from here by label alone.
        seen_labels: dict[str, int] = {}
        for f in fields:
            key = option_match.plain(f.label or f.question)
            seen_labels[key] = seen_labels.get(key, 0) + 1
        fillable = ("button_list", "select", "combobox", "date")
        for f in fields:
            if f.kind not in fillable or f.trap or not f.visible or f.disabled or not f.empty:
                continue
            if seen_labels.get(option_match.plain(f.label or f.question), 0) > 1:
                continue
            if f.kind != "button_list":
                question = re.sub(r"[\s*✱:]+$", "", f.label or f.question)
                if not question:
                    continue
                value, source = self.known_answer(Control(ref=f"inv:{f.id}", role="combobox", name=question,
                                                          options=list(f.options)))
                if not value:
                    continue
                done_it = form_fields.fill_date(tab, f, value) if f.kind == "date" else form_fields.choose(tab, f, value)
                if done_it:
                    logger.info("KNEW: %s = %r (%s) -- a %s the usual reading left empty", question[:44], value[:34],
                                source, f.kind.replace("_", " "))
                    self.written[question] = value
                    done.append(question)
                continue
            question = re.sub(r"[\s*✱:]+$", "", f.label or f.question)
            # The snapshot shows it as a question and can answer it the usual way -- unless what it shows is the
            # hidden one-option <select> that only mirrors the button (BambooHR): that has nothing to choose.
            if not question or any(_same_question(c.question, question)
                                   and ((c.role in ANSWER_ROLES and not (c.role in ("combobox", "listbox")
                                                                         and len(c.options) <= 1))
                                        or is_workday_choice_button(c)) for c in controls):
                continue
            value, source = self.known_answer(Control(ref=f"inv:{f.id}", role="combobox", name=question))
            if value and form_fields.choose_from_button_list(tab, f, value):
                logger.info("KNEW: %s = %r (%s) -- a list the page draws as a button", question[:44], value[:34],
                            source)
                self.written[question] = value
                done.append(question)
        return done

    def attach_documents(self, page, snapshot: str, controls: list[Control]) -> list[tuple[Answer, Control]]:
        """Attaches the resume and the cover letter where the form asks for them.

        Not left to the plan: Harbinger's form (Greenhouse) has a "Cover Letter"
        section whose only control is called "Attach", and the cover letter was
        forgotten again. A section that already holds a file is left alone.
        """
        given: list[tuple[Answer, Control]] = []
        wanted = (("resume", re.compile(r"resume|\bcv\b", re.IGNORECASE), "upload_resume"),
                  ("cover letter", re.compile(r"cover letter", re.IGNORECASE), "upload_cover_letter"))
        if self.resume_file and (self.resume_file.name in (snapshot or "") or
                                 _section_holds_a_file(snapshot, "resume")):
            self.resume_uploaded = True

        for what, matches, action in wanted:
            if action == "upload_resume" and (self.resume_uploaded or not self.resume_file):
                continue
            if action == "upload_resume" and self._attach_resume_to_its_input(self.tab(page)):
                given.append((Answer("", "resume", action, Path(self.resume_file).name, "document"),
                              Control(ref="", role="button", name="resume")))
                continue
            if action == "upload_cover_letter":
                # Task 4.3: Ensure cover letter generation is strictly lazy-loaded:
                # only trigger if an explicit, required cover letter target or text area is actively detected
                is_required = any(
                    matches.search(f"{c.container} {c.name}")
                    and ("*" in (c.name or "") or "*" in (c.container or "") or "*" in (c.question or "") or "required" in (c.context or "").lower())
                    for c in controls
                )
                if not is_required:
                    continue
                if self._letter_attached or self.cover_letter is None:
                    continue
            control = _upload_control(controls, matches) or self._file_input_for(page, controls, matches)
            # A required upload that never says "resume" beside it, on a page or step that is about the resume:
            # Avature's "Select your resume" step shows "From Device *  No file selected  [Choose another file]"
            # (Steelcase, 29 September) -- the agent pressed Continue three times with nothing attached.
            if control is None and action == "upload_resume" and (
                    _RESUME_PAGE.search(snapshot or "")
                    and re.search(r"required|error", snapshot or "", re.IGNORECASE)):
                generic_uploads = [c for c in controls
                                   if c.role in PRESS_ROLES | {"button"}
                                   and _FILE_BUTTON.search(c.name or "")]
                if len(generic_uploads) == 1:
                    control = generic_uploads[0]
            if control is None:
                continue
            if _section_holds_a_file(snapshot, control.container or control.question):
                if action == "upload_cover_letter":
                    self._letter_attached = True
                continue
            path = self.resume_file if action == "upload_resume" else self._letter_file()
            if not path:
                logger.info("The form asks for a %s and none could be prepared", what)
                continue
            answer = Answer(control.ref, control.question or what, action, Path(path).name, "document")
            try:
                done = self.do(page, answer, control)
            except Exception as exc:
                done = False
                logger.info("Could not attach the %s: %s", what, str(exc).splitlines()[0][:100])
                try:
                    self.tab(page).keyboard.press("Escape")   # a menu it opened instead is not left over the page
                except Exception:
                    pass
            if done:
                given.append((answer, control))
                if action == "upload_cover_letter":
                    self._letter_attached = True
                logger.info("ATTACHED: the %s (%s)", what, Path(path).name)
        return given

    def _file_input_for(self, page, controls: list[Control], section: re.Pattern) -> Optional[Control]:
        """A plain <input type=file> the section names ("Resume *").

        The snapshot draws it as a button named by its label, with no upload word,
        so it looks like any other "Resume" button. It used to be found only when
        the AI plan named it; a page answered from the profile alone skipped the
        plan and the resume was never attached. The page is asked what it is.
        """
        for c in controls:
            if c.role != "button" or c.disabled or not section.search(f"{c.container} {c.context} {c.name}"):
                continue
            try:
                if self.locate(page, c.ref).evaluate("e => e.tagName === 'INPUT' && e.type === 'file'",
                                                     timeout=2_000):
                    return c
            except Exception:
                continue
        return None

    def not_stuck(self, given: list[tuple[Answer, Control]], after: list[Control]) -> list[str]:
        """The answers the page does not show after all."""
        by_ref = {c.ref: c for c in after}
        by_question: dict[str, list[Control]] = {}
        for c in after:
            by_question.setdefault(c.question, []).append(c)
        missing: list[str] = []
        for answer, before in given:
            if answer.action in ("upload_resume", "upload_cover_letter"):
                continue   # a file shows up differently on every site; the submit check looks for it
            now = by_ref.get(before.ref) or next(iter(by_question.get(before.question, [])), None)
            if now is None:
                continue   # the page rebuilt itself; the next read will show it
            want = answer.value.strip().lower()
            if answer.action == "choose" and before.role in ("radio", "checkbox", "switch"):
                # A choice among buttons: whichever button carries the answer must be the one on.
                group = [c for c in after if c.role == before.role and before.group and c.group == before.group]
                ok = any(c.checked and _same_answer(c.name, answer.value) for c in group) or \
                    (not group and now.checked)
                if not ok:
                    missing.append(f"{before.question[:60]} = {answer.value[:40]!r}")
                continue
            if answer.action in ("check", "uncheck"):
                if now.role == "radio" and now.group:
                    ok = any(c.checked for c in by_question.get(now.group, []) if c.ref == before.ref) or now.checked
                else:
                    ok = now.checked
                ok = ok if answer.action == "check" else not ok
            else:
                shown = now.answer.strip().lower()
                digits = re.sub(r"\D", "", want)
                ok = bool(shown) and (want in shown or shown in want or _same_answer(shown, want)
                                      or (len(digits) >= 7 and digits[-10:] in re.sub(r"\D", "", shown)))
            if not ok:
                missing.append(f"{before.question[:60]} = {answer.value[:40]!r}")
        return missing

    def refusal(self, page, answer: Answer, control: Control, controls: list[Control] = (),
                correcting: bool = False) -> str:
        """Why this answer must not be given, or "" when it may."""
        question = control.question or answer.question
        action = answer.action
        if action not in ("fill", "choose", "check", "uncheck", "upload_resume", "upload_cover_letter"):
            return f"not something the agent does ({action})"
        if control.disabled:
            return "the field is disabled"
        if safety.is_attestation(question) or safety.is_attestation(control.name):
            privacy_ok = (action == "check" and safety.is_privacy_consent(question)
                          and getattr(self.profile, "accept_application_privacy_prompts", False))
            # The owner's decision (2026-09-17): the agent signs for them. A typed
            # signature is only ever the owner's own legal name.
            signing_ok = getattr(self.profile, "sign_attestations", False) and (
                action == "check" or (action == "fill" and _plain(answer.value) == _plain(
                    getattr(self.profile, "full_name", ""))))
            if not (privacy_ok or signing_ok):
                return "a declaration or signature -- you haven't allowed the agent to give it"
        # A blank the profile states on purpose (no middle name) is the answer: the AI's is refused. SK AX USA (ADP),
        # 30 September: the site's resume reader put "Reddy" in Middle Name, the agent cleared it as the profile says,
        # the AI was asked about the now-empty box and wrote "Reddy" back, and the run stopped on the tug of war.
        if action == "fill" and answer.value.strip() and not str(answer.source or "").startswith("profile.") \
                and self._profile_says_none(self.known_answer(control)[1]):
            return "your profile says there is none, so the box stays empty"
        # An optional box inside a job or degree entry that the owner's record does not fill is left blank: UKG's
        # education "Description" got the AI's "I have six years of experience ..." (30 September).
        if action == "fill" and not str(answer.source or "").startswith(("profile.", "owner", "work history")) \
                and self._optional_entry_box(control, ()):
            return "an optional box of a job or degree entry -- left blank rather than written up"
        # A tag box that already holds tags is answered: its text box stays empty beside the chips, and the page
        # plan kept asking to type into UKG's Skills (a fill timeout each time, 30 September).
        if action in ("fill", "choose") and control.role in ("textbox", "searchbox", "combobox") \
                and form_fields.tags_beside(self.locate(page, control.ref)):
            return "a tag box that already holds tags -- left as it is"
        # Who the owner is (gender identity, orientation, race, disability, veteran status): only their own answer.
        if concept_matcher.is_self_identification(f"{question} {control.container} {control.context}") \
                and not str(answer.source or "").startswith(("profile.", "owner", "answer_bank.you")):
            return "a question about who you are -- only your own answer is given, never the AI's guess"
        try:
            if (self.locate(page, control.ref).get_attribute("type", timeout=2_000) or "").lower() == "password":
                return "a password -- the agent never types those here"
        except Exception:
            pass
        if action in ("fill", "choose") and not answer.value.strip():
            # Emptying a box is an answer when the profile says the thing does
            # not exist: the owner has no middle name, and the site's import
            # had put one there.
            field_name = answer.source.split(".", 1)[1] if answer.source.startswith("profile.") else ""
            deliberate = bool(field_name) and hasattr(self.profile, field_name) \
                and not str(getattr(self.profile, field_name) or "").strip()
            if not deliberate:
                return "no answer to give"
        if safety.is_legal_status_question(question) or safety._SPONSORSHIP_Q.search(question) \
                or safety._AUTHORIZED_Q.search(question):
            field_name = answer.source.split(".", 1)[1] if answer.source.startswith("profile.") else ""
            # An exact entry in the local answer library is an explicit owner
            # profile answer too.  It is the route for employer-specific legal
            # wording that has no dedicated UserProfile attribute.
            approved_library_answer = answer.source == "profile.answer_library" and \
                bool(self.library_answer(question))
            if not approved_library_answer and (
                not field_name or not str(getattr(self.profile, field_name, "") or "").strip()
            ):
                return "a legal or immigration question your profile doesn't state"
        current = control.answer
        if self._owner_gave(question):
            return "you answered this yourself -- left as you set it"
        if correcting:
            # A correction may overrule the site, never a person: the
            # provenance observer saw the owner change this control.
            if provenance.owner_edited(self.locate(page, control.ref)) is True:
                return "you changed this yourself -- left as you set it"
            return ""
        if action in ("fill", "choose") and current:
            known_val, known_src = self.known_answer(control)
            if known_val and _same_answer(current, known_val):
                # Use exact comparison for the planner's value against the profile value:
                # _same_answer considers "Jane" ≈ "Jane Marie" via prefix matching,
                # but the profile's canonical value must be preserved exactly.
                if _plain(answer.value) != _plain(known_val):
                    return f"already answered from your profile ({current!r}) -- not changing to {answer.value!r}"
            if not self._ours(question, current):
                return f"already answered {current[:40]!r} -- not changing an answer the agent didn't give"
        if control.role == "radio" and action == "check" and control.group:
            chosen = next((c.name for c in controls if c.role == "radio" and c.group == control.group and c.checked), "")
            if chosen and not self._ours(question, chosen):
                return f"already answered {chosen[:40]!r} -- not changing an answer the agent didn't give"
            if chosen:
                known_val, _ = self.known_answer(control)
                if known_val and _same_answer(chosen, known_val) and not _same_answer(control.name, known_val):
                    return f"already answered from your profile ({chosen!r}) -- not changing to {control.name!r}"
                if not self._ours(question, chosen):
                    return f"already answered {chosen[:40]!r} -- not changing an answer the agent didn't give"
        if control.role in ("checkbox", "switch") and action == "uncheck" and control.checked                 and not self._ours(question, answer.value or "checked"):
            return "ticked by someone else -- not unticking it"
        return ""

    # -- putting pre-filled answers right ---------------------------------------------
    def correct_from_profile(self, page, plan: PagePlan, controls: list[Control]) -> list[tuple[Answer, Control]]:
        """Answers already on the page that contradict the owner's profile, put
        right from the profile.

        Schwab's questions page came with "No" to "will you require sponsorship"
        carried over from an old application. The owner's rule: the agent fixes
        it from the profile and carries on -- it is not left for the owner.
        Sponsorship and work authorization are corrected from their profile
        fields whatever Claude noticed; anything else Claude found contradicting
        a named profile field is corrected too. An answer the owner set is never
        touched.
        """
        # (question, right answer, profile field, must match exactly)
        fixes: list[tuple[str, str, str, bool]] = []
        # The owner's name as they write it. Forms were filled "Jane" /
        # "Marie Doe", split from the full name.
        for control in controls:
            if control.role not in ("textbox", "searchbox") or control.disabled or _holds_dial_code(control):
                continue
            field = _name_field(control.question) or _detail_field(control.question)
            if not field:
                continue
            wanted = str(getattr(self.profile, field, "") or "").strip()
            shown = control.answer.strip()
            if field == "middle_name" and not wanted and shown:
                # The owner has no middle name; the site's import put "Reddy"
                # there, which is part of their first name.
                replacement = "N/A" if "*" in control.question or "required" in control.question.lower() else ""
                fixes.append((control.question, replacement, "profile.middle_name", True))
                continue
            if wanted and shown.lower() != wanted.lower():
                # Exactly: "Jane" is not "Jane Marie", though one
                # begins the other.
                fixes.append((control.question, wanted, f"profile.{field}", True))
        # Where the owner lives, on any kind of control: a country, state or
        # city the site put there (a resume parser's guess, a list's first
        # entry) that contradicts the profile is put right from it -- country
        # first, since a state list depends on the country chosen. Never in a
        # work or education entry: those places belong to the entry, not to
        # the owner. A value the owner set is refused below. With
        # SITE_PREFILL_POLICY=leave the site's value stays and the owner is
        # told about it at the hand-over instead.
        policy = site_prefill_policy()
        located = []
        for control in controls:
            if control.role not in ("combobox", "listbox", "textbox", "searchbox") or control.disabled \
                    or _holds_dial_code(control):
                continue
            shown = control.answer.strip()
            if not shown or not control.question or _ENTRY_CONTEXT.search(
                    f"{control.question} {control.container} {control.context}"):
                continue
            concept = concept_matcher.confirm_concept(concept_matcher.match_concept(
                question=control.question, container=control.container,
                context=control.context, name=control.name), control.options)
            if concept not in _RESIDENCE_ORDER:
                continue
            wanted, source = self.known_answer(control)
            if not wanted or geo_reference.same_place(shown, wanted) or _same_answer(shown, wanted):
                continue
            who = provenance.origin(self.locate(page, control.ref), value=shown,
                                    agent_wrote=self._ours(control.question, shown))
            if not safety.may_overrule(who, policy):
                # The owner's own value stands silently; anything else they
                # should see before submitting.
                why = {provenance.SITE: "the site filled it and SITE_PREFILL_POLICY=leave",
                       provenance.UNKNOWN: "who set it could not be seen"}.get(who)
                if why:
                    self.notes.append(f"left {control.question[:70]!r} as {shown[:40]!r} ({why}); "
                                      f"your {source} says {wanted[:40]!r}")
                continue
            located.append((_RESIDENCE_ORDER[concept], control.question, wanted, source))
        for _order, question, wanted, source in sorted(located):
            if not any(_same_question(question, q) for q, _v, _s, _e in fixes):
                fixes.append((question, wanted, source, False))
        for item in answered_fields(controls):
            question = item["label"]
            if not question or not safety.legal_answer_conflicts([item], self.profile):
                continue
            if safety._SPONSORSHIP_Q.search(question):
                needs = bool(getattr(self.profile, "requires_visa_sponsorship", False))
                fixes.append((question, "Yes" if needs else "No", "profile.requires_visa_sponsorship", False))
            elif safety._AUTHORIZED_Q.search(question):
                allowed = str(getattr(self.profile, "legally_eligible_to_work", "") or "").lower().startswith("y")
                fixes.append((question, "Yes" if allowed else "No", "profile.legally_eligible_to_work", False))
        for mismatch in plan.mismatches:
            question = " ".join(str(mismatch.get("question") or "").split())
            value = str(mismatch.get("correct_value") or "").strip()
            source = str(mismatch.get("source") or "").strip()
            field_name = source.split(".", 1)[1] if source.startswith("profile.") else ""
            if not question or not value or not field_name or not str(getattr(self.profile, field_name, "") or "").strip():
                continue
            if any(_same_question(question, q) for q, _v, _s, _e in fixes):
                continue
            fixes.append((question, value, source, False))
        # Who the owner is (gender, race, veteran, disability) shown otherwise than the profile says: put right, under
        # the one rule for who may be overruled -- never a choice the owner made. Meta, 30 September: the agent's own
        # (since fixed) fallback chose Female for an owner whose profile says Male, and nothing ever looked again.
        for control in controls:
            question = control.question
            if control.role != "radio" or not control.checked or not control.name or not question \
                    or not concept_matcher.is_self_identification(question) \
                    or any(_same_question(question, q) for q, _v, _s, _e in fixes):
                continue
            value, source = self.known_answer(control)
            if not value or not str(source).startswith("profile.") \
                    or self.assistant._best_option([control.name], [value]) is not None:
                continue
            who = provenance.origin(self.locate(page, control.ref), value=control.name,
                                    agent_wrote=self._ours(question, control.name))
            if not safety.may_overrule(who, policy):
                if who != provenance.OWNER:
                    self.notes.append(f"left {question[:70]!r} as {control.name[:40]!r}; your {source} says "
                                      f"{value[:40]!r}")
                continue
            fixes.append((question, value, source, False))

        given: list[tuple[Answer, Control]] = []
        for question, value, source, exact in fixes:
            control = self._control_for(question, value, controls)
            if control is None:
                continue
            was = self._shown_answer(control, controls)
            if was and (_plain(was) == _plain(value) if exact else _same_answer(was, value)):
                continue
            action = ("check" if control.role in ("radio", "checkbox", "switch")
                      else "choose" if control.role in ("combobox", "listbox") and value else "fill")
            answer = Answer(control.ref, control.question, action, value, source)
            refusal = self.refusal(page, answer, control, controls, correcting=True)
            if refusal:
                logger.info("NOT CORRECTED: %r -- %s", control.question[:70], refusal)
                continue
            try:
                done = self.do(page, answer, control)
            except Exception as exc:
                done = False
                logger.info("Could not correct %r: %s", control.question[:60], str(exc).splitlines()[0][:100])
            if done:
                given.append((answer, control))
                self.written[control.question] = value
                self.corrected.add(control.question)
                note = f"corrected {control.question[:70]!r} from {was[:40]!r} to {value[:40]!r} (from your {source})"
                self.notes.append(note)
                logger.info("CORRECTED: %s", note)
        return given + self._correct_entries(page, controls, policy) + self._clear_none_boxes(page, controls, policy)

    def _clear_none_boxes(self, page, controls: list[Control], policy: str) -> list[tuple[Answer, Control]]:
        """A box the profile says has no answer (BLANK_MEANS_NONE) that holds one anyway is emptied.

        UKG, 30 September: before the profile knew "Address 2", the AI wrote "Fairfax, VA" into it; the new rule
        stopped it being written again but left it there. Emptied under safety.may_overrule -- never the owner's."""
        given: list[tuple[Answer, Control]] = []
        for control in controls:
            shown = (control.answer or "").strip()
            if control.role not in ("textbox", "searchbox") or control.disabled or not shown \
                    or control.ref in (getattr(self, "_entries", {}) or {}):
                continue
            value, source = self.known_answer(control)
            if value or not self._profile_says_none(source):
                continue
            written = str((self.written or {}).get(control.question) or "").strip()
            who = provenance.origin(self.locate(page, control.ref), value=shown, agent_wrote=written == shown)
            if not safety.may_overrule(who, policy):
                continue
            try:
                done = self._empty_box(page, control)
            except Exception as exc:
                done = False
                logger.info("Could not empty %r: %s", control.question[:60], str(exc).splitlines()[0][:100])
            if done:
                given.append((Answer(control.ref, control.question, "fill", "", source), control))
                self.written[control.question] = ""
                self.corrected.add(control.question)
                note = f"emptied {control.question[:70]!r} ({shown[:40]!r}): your {source} says there is none"
                self.notes.append(note)
                logger.info("CORRECTED: %s", note)
        return given

    def _correct_entries(self, page, controls: list[Control], policy: str) -> list[tuple[Answer, Control]]:
        """A box in a job or degree entry that holds what that entry's record says it should not.

        UKG, 30 September: the site's resume import put "Sep 2026" as the end of the current job and of a job that
        ended in July 2021, and the agent's own AI text sat in a degree's optional Description. The agent only
        told the owner to fix them. Each box is put right from its own entry's record -- or emptied, when the right
        answer is nothing (the job the owner still has; a box the agent filled that should have stayed empty) --
        under the one rule for who may be overruled (safety.may_overrule): never a value the owner set."""
        given: list[tuple[Answer, Control]] = []
        entries = getattr(self, "_entries", {}) or {}
        for control in controls:
            entry = entries.get(control.ref)
            shown = (control.answer or "").strip().strip('"')
            if entry is None or control.disabled or not shown or option_match.is_placeholder(shown):
                continue
            value, source, blank = repeated_entries.answer(entry, getattr(self, "history", {}) or {})
            # Only a date is put right from the record: a school's or degree's name has many right spellings
            # ("Jawaharlal Nehru Technological University" is "JNTU"), and a label like "Role Description" is not
            # the job's title -- replayed on every saved page, those would have overwritten right answers.
            if value and not repeated_entries.is_date(value):
                continue
            written = str((self.written or {}).get(control.question) or "").strip()
            if value:
                if _same_answer(shown, value) or option_match.best_option([shown], value) is not None:
                    continue
            elif not (blank or (written and written == shown and self._optional_entry_box(control, ())
                                and repeated_entries.understands(entry))):
                # Unknown is not empty: a bare "Year" the record cannot place (From or To?) keeps what was written.
                # Ciena, 30 September: the JNTU "To" year, filled from the profile, was emptied as "should be empty".
                continue
            who = provenance.origin(self.locate(page, control.ref), value=shown, agent_wrote=written == shown)
            if not safety.may_overrule(who, policy):
                if who != provenance.OWNER:
                    self.notes.append(f"left {entry.label!r} in {repeated_entries.describe(entry, self.history)!r} "
                                      f"as {shown[:40]!r}; it should be {value or 'empty'!r}")
                continue
            try:
                if value:
                    action = "choose" if control.role in ("combobox", "listbox") else "fill"
                    answer = Answer(control.ref, control.question, action, value, source)
                    done = self.do(page, answer, control)
                else:
                    answer = Answer(control.ref, control.question, "fill", "", source or "work history")
                    done = self._empty_box(page, control)
            except Exception as exc:
                done = False
                logger.info("Could not correct %r: %s", control.question[:60], str(exc).splitlines()[0][:100])
            if done:
                given.append((answer, control))
                self.written[control.question] = value
                self.corrected.add(control.question)
                where = repeated_entries.describe(entry, self.history) or f"entry {entry.index + 1}"
                note = f"corrected {entry.label!r} in {where!r} from {shown[:40]!r} to {value[:40] or 'empty'!r}"
                self.notes.append(note)
                logger.info("CORRECTED: %s", note)
        return given

    def _empty_box(self, page, control: Control) -> bool:
        """Empties a text box, or sets a list back to its own "Choose..." row; True when it then shows nothing."""
        loc = self.locate(page, control.ref)
        if control.role in ("combobox", "listbox"):
            blank = next((o for o in control.options or () if option_match.is_placeholder(o)), None)
            if blank is None:
                return False
            loc.select_option(label=blank, timeout=5_000)     # fires input and change itself
        else:
            fill_and_dispatch(loc, "", timeout=5_000)
        shown = loc.evaluate("e => e.tagName === 'SELECT' ? (e.options[e.selectedIndex] || {}).text || '' : e.value")
        return not str(shown or "").strip() or option_match.is_placeholder(str(shown))

    def _control_for(self, question: str, value: str, controls: list[Control]) -> Optional[Control]:
        matches = [c for c in controls if c.role in ANSWER_ROLES and _same_question(c.question, question)]
        radios = [c for c in matches if c.role == "radio"]
        if radios:
            index = self.assistant._best_option([c.name for c in radios], [value])
            return radios[index] if index is not None else None
        return matches[0] if matches else None

    @staticmethod
    def _shown_answer(control: Control, controls: list[Control]) -> str:
        if control.role == "radio":
            return next((c.name for c in controls if c.role == "radio" and c.group == control.group and c.checked), "")
        return control.answer

    # -- the owner's own answers ----------------------------------------------------------
    def remember_page_state(self, page) -> None:
        """What the page shows as the agent stops to wait, so anything the owner
        changes meanwhile is known to be theirs."""
        provenance.set_agent_busy(self.tab(page), False)
        try:
            self._paused_state = {f["label"]: f["value"] for f in answered_fields(parse_snapshot(self.snapshot(page)))}
        except Exception:
            self._paused_state = {}

    def note_owner_changes(self, page) -> None:
        provenance.set_agent_busy(self.tab(page), True)
        try:
            now = {f["label"]: f["value"] for f in answered_fields(parse_snapshot(self.snapshot(page)))}
        except Exception:
            return
        for question, value in now.items():
            if any(w and (_plain(w) in _plain(value) or _plain(value) in _plain(w))
                   for w in self.written.values()):
                continue   # the site tidied up what the agent wrote ("62701" -> "62701, Springfield, IL")
            if question in self._paused_state and value != self._paused_state[question]:
                self.owner_answers[question] = value
                logger.info("YOURS: %r is now %r -- the agent leaves it as you set it", question[:60], value[:40])
                self._keep_owner_answer(page, question, value)

    def _keep_owner_answer(self, page, question: str, value: str) -> None:
        """Saves, at once, an answer the owner gave while the run waited for them.

        It used to live only in this run's memory: answers were learned at the Review page, from what that page
        shows, and a question answered on step 3 is not on it -- so the same question came back on the next
        form. The rules are the learning step's: never a legal, visa or signed answer; a general question
        answered in a few words joins the saved answers (profile_setup.worth_keeping); every answer is also
        kept against this application, for this employer's next form."""
        if safety.is_attestation(question) or safety.is_legal_status_question(question) or safety.is_attestation(value):
            return
        if self.tracker is not None and self.key and hasattr(self.tracker, "record_answer"):
            try:
                self.tracker.record_answer(self.key, self._learning_host() or urlparse(self.tab(page).url).netloc,
                                           question, value, answered_by="user")
            except Exception as exc:
                logger.debug("Could not keep %r for this employer: %s", question[:50], exc)
        try:
            import profile_setup
            if profile_setup.remember_answer(question, value, getattr(self.job, "company", "") or ""):
                logger.info("SAVED ANSWER: %r = %r, for every application", question[:60], value[:40])
        except Exception as exc:
            logger.debug("Could not add %r to the saved answers: %s", question[:50], exc)

    def _owner_gave(self, question: str) -> bool:
        return any(_same_question(question, q) for q in self.owner_answers)

    def _ours(self, question: str, current: str) -> bool:
        """Whether what a box shows is the agent's own doing.

        A box the agent has written to during this run is its own whatever it
        now shows: Workday's year spinbutton was left reading "2012" by the
        agent's own failed attempt, and the agent then refused to put it right
        because it did not recognise the value as one of its own. What the
        owner typed himself is held in owner_answers and protected separately.
        """
        return question in self.written

    def do(self, page, answer: Answer, control: Control) -> bool:
        tab = self.tab(page)
        # Only typed text: a choice can only ever take one of the list's own options, and "N/A" or "None" is
        # often the right one -- even when the list shows its options only once it is opened.
        if answer.action == "fill" and (answer.value or "").strip() and _WEB_ADDRESS_BOX.search(control.question or "") \
                and not _WEB_ADDRESS.fullmatch((answer.value or "").strip()):
            # Ciena on Workday, 30 September: the AI read "Facebook" under Social Network URLs as "have you worked at
            # Facebook?" and its sentence was typed into the box. A box for a web address takes only a web address.
            logger.info("NOT TYPED: %r for %r -- a box for a web address takes only a web address",
                        answer.value[:60], control.question[:60])
            return False
        if answer.action == "fill" and is_non_answer(answer.value):
            # Whoever proposed it -- the page plan or a single question -- "Not provided in the resume" is not the
            # owner's answer and is never typed (Steelcase's Work Phone, 29 September).
            logger.info("NOT TYPED: %r for %r is a way of saying 'unknown'", answer.value[:60], control.question[:60])
            return False
        if answer.action == "choose":
            self._choice_methods.pop(control.ref, None)
            recipe = self._recalled_form_recipe(control, answer)
            if recipe is not None:
                identity = self._recipe_identity(control, answer)
                method = str(recipe.get("method") or "")
                if method == "type_and_commit":
                    if self.type_and_commit(page, control, answer.value):
                        self._choice_methods[control.ref] = method
                        if identity is not None:
                            self._reused_recipes[control.ref] = identity
                        return True
                    if identity is not None:
                        self._mark_recipe_failed(identity)
                elif method == "native_select" and control.options:
                    index = self.assistant._best_option(control.options, [answer.value])
                    if index is not None:
                        try:
                            self.locate(page, control.ref).select_option(
                                label=control.options[index], timeout=5_000
                            )
                            self._choice_methods[control.ref] = method
                            if identity is not None:
                                self._reused_recipes[control.ref] = identity
                            return True
                        except Exception:
                            if identity is not None:
                                self._mark_recipe_failed(identity)
        loc = self.locate(page, control.ref)
        if control.holds_choices and answer.action in ("choose", "check", "fill"):
            # The choices have no reference of their own: click the one that
            # says what the answer says, inside the group.
            return self._choose_inside(page, control, answer.value)
        if answer.action == "fill" and control.role == "spinbutton":
            # A spinbutton keeps its own count: setting its text leaves the
            # widget on the value it had (Workday's year box stayed on 2012),
            # so the digits are typed in as a person types them.
            # Workday's date boxes take keystrokes but refuse a click, and a
            # value set into them does not stay: the month came out as 2
            # whatever was typed. Each way is tried until the box really holds
            # what was asked for.
            return self.put_in_a_spinbutton(page, loc, answer.value)
        if answer.action == "fill":
            # A date box is found before fill(): Playwright's fill() raises
            # "Malformed value" for a non-ISO string on a date widget, and a
            # text box that shows its format takes only that format.
            fill_value = answer.value
            try:
                if loc.is_disabled(timeout=1_000) or not loc.is_visible(timeout=1_000):
                    logger.info("Field %r is disabled or hidden -- skipped", control.question[:50])
                    return True
            except Exception:
                pass
            try:
                input_type, placeholder = loc.evaluate(
                    "el => [el.tagName === 'INPUT' ? (el.type || '') : '', el.getAttribute('placeholder') || '']",
                    timeout=2_000)
            except Exception:
                input_type, placeholder = "", ""
            # A date goes into a date box the one way every date is written (form_fields.date_text): in the box's own
            # format, whatever the answer's ("December 2022", "2 weeks", "12/15/2022"). An answer that is not a date
            # is not written into a date box: before, today's date went in its place.
            if form_fields.is_date_box(input_type, placeholder):
                written = form_fields.date_text(fill_value, input_type, placeholder)
                if written is None:
                    logger.info("DATE FIELD: %r is not a date, for %r -- left open", fill_value[:40],
                                control.question[:50])
                    return False
                fill_value = written
            try:
                fill_and_dispatch(loc, fill_value, timeout=2_500)
            except Exception:
                try:
                    is_inert = loc.evaluate("""(el) => {
                        return el.disabled || el.readOnly || el.getAttribute('aria-disabled') === 'true' ||
                               Boolean(el.closest('[disabled], [aria-disabled="true"], .ant-picker-disabled, [aria-readonly="true"]')) ||
                               window.getComputedStyle(el).pointerEvents === 'none' ||
                               window.getComputedStyle(el).display === 'none' ||
                               window.getComputedStyle(el).visibility === 'hidden';
                    }""", timeout=1_000)
                    if is_inert:
                        logger.info("Field %r is inert/disabled -- skipped", control.question[:50])
                        return True
                except Exception:
                    pass
                try:
                    if loc.is_disabled(timeout=1_000):
                        logger.info("Field %r is disabled -- skipped", control.question[:50])
                        return True
                except Exception:
                    pass
                try:
                    loc.evaluate("""(el, value) => {
                        const setter = Object.getOwnPropertyDescriptor(
                            HTMLInputElement.prototype, 'value') ?
                            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set : null;
                        if (setter) {
                            setter.call(el, value);
                        } else {
                            el.value = value;
                        }
                        el.dispatchEvent(new InputEvent('input', {bubbles: true, composed: true, inputType: 'insertText', data: value}));
                        el.dispatchEvent(new Event('change', {bubbles: true, composed: true}));
                    }""", fill_value, timeout=1_000)
                    logger.info("Field %r filled via JS fallback after loc.fill failure", control.question[:50])
                    return True
                except Exception:
                    pass
                raise
            try:
                loc.press("Tab", timeout=2_000)
            except Exception:
                pass
            if re.fullmatch(r"city|zip code|postal code", control.question.strip(), re.IGNORECASE):
                try:
                    tab.wait_for_timeout(300)
                    if (loc.input_value(timeout=2_000) or "").strip() != fill_value.strip():
                        loc.evaluate("""(el, value) => {
                            const setter = Object.getOwnPropertyDescriptor(
                                HTMLInputElement.prototype, 'value').set;
                            setter.call(el, value);
                            el.dispatchEvent(new InputEvent('input', {bubbles: true, composed: true, inputType: 'insertText', data: value}));
                            el.dispatchEvent(new Event('change', {bubbles: true, composed: true}));
                            el.blur();
                        }""", fill_value)
                        tab.wait_for_timeout(300)
                except Exception:
                    pass
            return True
        if answer.action in ("check", "uncheck"):
            # A tick box drawn as a hidden input behind a styled label (ADP's "Yes, I agree to sign electronically.",
            # SK AX USA, 30 September) refuses set_checked and a click on the input: its label is clicked, then the
            # input's own click() is dispatched. Done only when the box then shows the state asked for.
            want = answer.action == "check"
            for attempt in (lambda: loc.set_checked(want, timeout=5_000),
                            lambda: loc.click(timeout=3_000),
                            lambda: loc.evaluate(_CLICK_LABEL_JS),
                            lambda: loc.evaluate("e => e.click()")):
                try:
                    attempt()
                except Exception:
                    continue
                if _is_checked(loc) == want:
                    return True
            return _is_checked(loc) == want
        if answer.action in ("upload_resume", "upload_cover_letter"):
            if answer.action == "upload_resume":
                path = self.resume_file
            else:
                path = self._letter_file()
            if not path or not Path(path).is_file():
                return False
            # Workday's "Select files" button is only a visual trigger; its
            # real file input is hidden inside the upload widget and does not
            # reliably emit Playwright's filechooser event. The same for a cover letter's.
            if answer.action in ("upload_resume", "upload_cover_letter"):
                try:
                    for depth in range(1, 8):
                        nearby = loc.locator(
                            f"xpath=ancestor::*[{depth}]"
                        ).locator("input[type='file']")
                        if nearby.count():
                            nearby.last.set_input_files(str(path), timeout=15_000)
                            if answer.action == "upload_resume":
                                self._resume_went_on(path)
                            self.settle(page, 1_500)
                            return True
                except Exception as exc:
                    logger.info("Nearby file input was unavailable: %s", str(exc).splitlines()[0][:120])
            if answer.action == "upload_resume" and hasattr(self.assistant, "upload_via_chooser"):
                try:
                    if self.assistant.upload_via_chooser(
                            page, r"select files?|upload a file|choose files?", Path(path)):
                        self._resume_went_on(path)
                        self.settle(page, 1_500)
                        return True
                except Exception as exc:
                    logger.info("Browser upload helper could not attach the resume: %s", str(exc).splitlines()[0][:120])
            try:
                loc.set_input_files(str(path), timeout=8_000)
            except Exception:
                try:
                    with tab.expect_file_chooser(timeout=4_000) as chooser:
                        loc.click(timeout=5_000)
                    chooser.value.set_files(str(path))
                except Exception:
                    # The button opened a menu of sources, not a file window (Jobvite: Dropbox / File / Type or Paste
                    # Resume / Apply With LinkedIn -- Altamira, 30 September): its item for a file on this computer is
                    # chosen, never a cloud drive or a sign-in, and the file goes to the window that item opens.
                    item = next((el for el in (tab.get_by_text(_FILE_ON_THIS_COMPUTER).nth(i) for i in range(
                        min(tab.get_by_text(_FILE_ON_THIS_COMPUTER).count(), 6))) if el.is_visible()), None)
                    if item is None:
                        raise
                    with tab.expect_file_chooser(timeout=8_000) as chooser:
                        item.click(timeout=5_000)
                    chooser.value.set_files(str(path))
                    logger.info("ATTACHED: the %s, through the upload menu's %r",
                                "resume" if answer.action == "upload_resume" else "cover letter",
                                (item.inner_text() or "").strip()[:30])
            if answer.action == "upload_resume":
                self._resume_went_on(path)
            self.settle(page, 1_500)
            return True
        if answer.action == "choose" and control.role in ("textbox", "searchbox", "spinbutton"):
            fill_and_dispatch(loc, answer.value, timeout=8_000)     # a plain box, whatever the plan called it
            return True
        if answer.action in ("choose", "check") and control.role in ("radio", "checkbox", "switch", "group", "radiogroup"):
            snapshot_controls = parse_snapshot(self.snapshot(page))
            if control.role in ("group", "radiogroup"):
                group = [c for c in snapshot_controls
                         if c.role in ("radio", "checkbox", "switch") and (c.group == control.question or c.container == control.question)]
                if not group:
                    group = [c for c in snapshot_controls if c.role in ("radio", "checkbox", "switch")]
            else:
                group = [c for c in snapshot_controls
                         if c.role == control.role and c.group and c.group == control.group] or [control]
            if control.toggle and answer.action == "check" and answer.value.strip().lower() in ("checked", "true", ""):
                return self._press_toggle(page, control)
            # A question's own choices were found: the answer is one of them or none. Looking further -- every
            # radio on the page, the first label that contains the words -- ticked another question's "Yes".
            real_group = len(group) >= 2
            if control.role == "checkbox" and real_group \
                    and answer.value.strip().lower() not in ("checked", "true", "unchecked", "false", ""):
                return self._tick_several(page, group, answer.value)
            index = self.assistant._best_option([c.name for c in group], [answer.value])
            if control.toggle or any(c.toggle for c in group):
                if index is None:
                    logger.info("No choice matching %r for %r among %s", answer.value, control.question[:50],
                                [c.name for c in group][:8])
                    return False
                return self._press_toggle(page, group[index])
            if index is None and not real_group:
                role_filter = control.role if control.role in ("radio", "checkbox", "switch") else "radio"
                all_radios = [c for c in snapshot_controls if c.role == role_filter]
                fallback_idx = self.assistant._best_option([c.name for c in all_radios], [answer.value])
                if fallback_idx is not None:
                    group = all_radios
                    index = fallback_idx
            if index is None and not real_group:
                try:
                    candidates = [answer.value]
                    if re.search(r"\bnot\b.*\bveteran\b", answer.value, re.I):
                        candidates.extend(["I am not a protected veteran", "Not a protected veteran", "No"])
                    for cand in candidates:
                        role_sel = control.role if control.role in ("radio", "checkbox") else "radio"
                        # The whole text, not a part of it: "Male" is inside "Female", and a label holding a whole
                        # group was clicked in its middle -- Meta's gender went to Female (30 September).
                        whole = re.compile(rf"^\s*{re.escape(cand)}\s*$", re.IGNORECASE)
                        radio_loc = tab.locator(f"input[type='{role_sel}']").filter(has_text=whole).first
                        if not radio_loc.count():
                            radio_loc = tab.locator("label").filter(has_text=whole).first
                        if radio_loc.count():
                            try:
                                radio_loc.click(timeout=3_000)
                            except Exception:
                                radio_loc.click(force=True, timeout=2_000)
                            return True
                except Exception:
                    pass
                logger.info("No choice matching %r for %r among %s", answer.value, control.question[:50],
                            [c.name for c in group][:8])
                return False
            if index is None:
                # The question's own choices do not hold the answer: it is left for the owner, not looked for
                # elsewhere on the page -- and never clicked as choice number None, which crashed UKG's OPT/STEM
                # question three times (30 September).
                logger.info("No choice matching %r for %r among %s", answer.value, control.question[:50],
                            [c.name for c in group][:8])
                return False
            target = self.locate(page, group[index].ref)
            try:
                target.set_checked(True, timeout=5_000)
            except Exception:
                target.click(timeout=5_000)
                try:
                    target.click(timeout=3_000)
                except Exception:
                    try:
                        target.click(force=True, timeout=2_000)
                    except Exception:
                        target.evaluate("e => e.click()")
            return True
        if answer.action == "choose":
            return self.choose(page, control, answer.value, self._page_controls(page))
        return False

    def _optional_entry_box(self, control: Control, still_open) -> bool:
        """A box inside a job or degree entry that the form does not mark required."""
        if getattr(self, "_entries", {}).get(control.ref) is None:
            return False
        if _REQUIRED_STAR.search(control.question or "") or _REQUIRED_STAR.search(control.name or ""):
            return False
        return not any(q.endswith("*") and _same_question(q.rstrip("*"), control.question) for q in still_open or ())

    def _profile_says_none(self, source: str) -> bool:
        """Whether a blank answer is the profile's own word: its field exists and is empty on purpose (no middle
        name), as against a question the profile knows nothing about."""
        field = str(source or "").split(".", 1)[1] if str(source or "").startswith("profile.") else ""
        return field in BLANK_MEANS_NONE and hasattr(self.profile, field) \
            and not str(getattr(self.profile, field) or "").strip()

    def _inventory_field(self, page, control: Control):
        """The field inventory's entry for this list, found by its question -- or None when there is not exactly
        one."""
        try:
            fields = form_fields.inventory(self.tab(page))
        except Exception:
            return None
        found = [f for f in fields if f.kind in ("combobox", "select", "button_list") and f.visible and not f.trap
                 and _same_question(f.question or f.label, control.question)]
        return found[0] if len(found) == 1 else None

    def answer_location_choices(self, page, controls: list[Control]) -> int:
        """Answers a question asking which of its places the owner would like to work or apply in, by the owner's
        rule (location_choice.py): tick boxes take the owner's own state's places and, open to relocation, the rest;
        a pick-one list or radio row takes the owner's state's place first. Only a question nothing has answered yet
        -- never one the owner or the site already answered. Returns how many questions were answered."""
        answered = 0
        groups: dict[tuple[str, str], list[Control]] = {}
        for c in controls:
            # The question over a row of boxes: their group, or the fieldset they sit in.
            asked = c.group or c.container
            if c.role in ("checkbox", "radio") and asked and not c.disabled:
                groups.setdefault((c.role, asked), []).append(c)
        for (role, question), boxes in groups.items():
            if len(boxes) < 2 or any(b.checked for b in boxes):
                continue
            picks = location_choice.choices(question, [b.name for b in boxes], self.profile, several=role == "checkbox")
            if not picks:
                continue
            done = []
            for k in picks:
                try:
                    target = self.locate(page, boxes[k].ref)
                    try:
                        target.set_checked(True, timeout=5_000)
                    except Exception:
                        target.click(timeout=5_000)
                    done.append(boxes[k].name)
                except Exception as exc:
                    logger.info("Could not tick %r: %s", boxes[k].name, str(exc).splitlines()[0][:100])
            if done:
                answered += 1
                self.written[question] = "; ".join(done)
                logger.info("KNEW: %s = %s (your state first%s)", question[:50], "; ".join(done)[:120],
                            ", then anywhere -- open to relocation" if len(done) > 1 else "")
        for c in controls:
            if c.role not in ("combobox", "listbox") or c.disabled or c.answer or len(c.options) < 2:
                continue
            picks = location_choice.choices(c.question, list(c.options), self.profile, several=False)
            if not picks:
                continue
            answer = Answer(c.ref, c.question, "choose", c.options[picks[0]], "profile.locations")
            try:
                if self.do(page, answer, c):
                    answered += 1
                    self.written[c.question] = answer.value
                    logger.info("KNEW: %s = %r (your state first)", c.question[:50], answer.value)
            except Exception as exc:
                logger.info("Could not choose %r: %s", answer.value, str(exc).splitlines()[0][:100])
        return answered

    def _tick_several(self, page, boxes: list[Control], value: str) -> bool:
        """Ticks the boxes a "select any that apply" answer names: the whole answer when it is one of them, else each
        part of it ("CCNP; NSE7", "English, Telugu"). Every part must be one of the boxes, or nothing is ticked and
        the question stays open -- never a box that is only like the answer."""
        names = [box.name for box in boxes]
        whole = self.assistant._best_option(names, [value])
        picks = [whole] if whole is not None else []
        if not picks:
            parts = [part for part in re.split(r"\s*(?:;|\n|\|)\s*|\s*,\s+", value) if part.strip()]
            picks = [self.assistant._best_option(names, [part]) for part in parts]
            if len(parts) < 2 or any(pick is None for pick in picks):
                logger.info("No choices matching %r for %r among %s", value, boxes[0].question[:50], names[:8])
                return False
        for pick in dict.fromkeys(picks):
            if boxes[pick].checked:
                continue
            target = self.locate(page, boxes[pick].ref)
            try:
                target.set_checked(True, timeout=5_000)
            except Exception:
                target.click(timeout=5_000)
        return True

    def _press_toggle(self, page, choice: Control) -> bool:
        """Press a choice drawn as a toggle button once, and read it back.

        Clicking a pressed toggle clears it (Ashby's Yes/No), so a pressed
        choice is left alone and a click that did not take is never followed
        by another: the question is reported open instead.
        """
        if choice.checked:
            return True
        try:
            self.locate(page, choice.ref).click(timeout=5_000)
        except Exception as exc:
            logger.info("TOGGLE: could not press %r for %r: %s", choice.name, choice.group[:60],
                        str(exc).splitlines()[0][:100])
        self.settle(page, 500)
        now = next((c for c in parse_snapshot(self.snapshot(page))
                    if c.toggle and c.group == choice.group and c.name == choice.name), None)
        if now is None or not now.checked:
            logger.info("TOGGLE: %r did not stay pressed for %r", choice.name, choice.group[:60])
            return False
        return True

    def _alternatives_for(self, value: str) -> list[str]:
        """The substitutes the OWNER approved for this value (profile.answer_alternatives), in their order.
        Nothing else is ever substituted: a field of study is a fact the application certifies."""
        mapping = getattr(self.profile, "answer_alternatives", None) or {}
        key = _plain(value)
        for name, alternatives in mapping.items():
            if _plain(str(name)) == key and isinstance(alternatives, (list, tuple)):
                return [str(a) for a in alternatives if str(a).strip() and _plain(str(a)) != key]
        return []

    def choose(self, page, control: Control, value: str, controls: list[Control] = ()) -> bool:
        """Chooses `value` from the control's list. Where the list does not offer it, tries the
        substitutes the owner has approved for that value, in order, and nothing else; the hand-over
        says when one was used."""
        if self._choose_exact(page, control, value, controls):
            return True
        for alternative in self._alternatives_for(value):
            if self._choose_exact(page, control, alternative, controls):
                self._chosen_instead[control.ref] = alternative
                note = (f"{(control.question or control.name)[:60]}: {value!r} is not offered, so your listed "
                        f"alternative {alternative!r} was chosen")
                if note not in self.notes:
                    self.notes.append(note)
                logger.info("CHOICE: %s", note)
                return True
        return False

    def _choose_exact(self, page, control: Control, value: str, controls: list[Control] = ()) -> bool:
        tab = self.tab(page)
        loc = self.locate(page, control.ref)
        self._choice_methods.pop(control.ref, None)
        # Some widgets only open from a button of their own ("Open the drop-down
        # list for Employer State or Province."), and typing into the box alone
        # leaves the form saying the field is empty.
        opener = next((c for c in controls if c.role in PRESS_ROLES and re.search(
            r"open the (drop-?down )?list for", c.name or "", re.IGNORECASE)
            and _same_question(re.sub(r"^.*list for\s*|[.]$", "", c.name or "", flags=re.IGNORECASE),
                               control.question)), None)
        if opener is not None:
            try:
                showing = {ref for ref, _t in choices_in(self.snapshot(page), under=control.question,
                                                         any_list=True)}
                self.locate(page, opener.ref).click(timeout=4_000)
                # A list the site fetches when it opens (R+L's states, once the
                # country is set) takes its time; four seconds was not enough
                # and the answer was typed into the box instead, which the form
                # then refused as empty.
                offered = [c for c in self._wait_for_choices(page, seconds=12.0, under=control.question,
                                                             any_list=True) if c[0] not in showing]
                index = self._pick(offered, value)
                if index is not None:
                    self.locate(page, offered[index][0]).click(timeout=5_000)
                    tab.wait_for_timeout(500)
                    self._choice_methods[control.ref] = "opened_list"
                    return True
            except Exception as exc:
                logger.debug("The list button did not help: %s", str(exc).splitlines()[0][:90])
        best = self.assistant._best_option
        if control.options:
            index = best(control.options, [value])
            if index is None:
                logger.info("No choice matching %r for %r among %s", value, control.question[:50], control.options[:8])
                return False
            try:
                loc.select_option(label=control.options[index], timeout=5_000)
                self._choice_methods[control.ref] = "native_select"
                return True
            except Exception:
                pass   # a listbox drawn by script: pick the option instead

        # A list whose choices exist only once it is opened -- Greenhouse's type-to-search lists, a server's
        # suggestions -- is filled by the field inventory's filler, which opens it the way its portal builds it,
        # reads the rows, clicks the one that is the answer and confirms it (form_fields.choose). Lucid's gender
        # and disability lists were left empty on three passes (29 September). When it does not take, the ways
        # below that other portals rely on (Workday's prompt lists, Ant lists) are tried as before.
        if not control.options and control.role == "combobox":
            field = self._inventory_field(page, control)
            if field is not None and form_fields.choose(tab, field, value):
                self._choice_methods[control.ref] = "inventory_filler"
                return True

        # An Ant Design list (Dayforce): read whole, chosen by label, and
        # confirmed by what the select then shows (interaction.resolve_ant_dropdown).
        # A single-choice list takes nothing but its own options, so when the
        # answer is not among them nothing else is tried: typing and pressing
        # Enter picks whichever row is first -- that is how "United States"
        # became "Afghanistan". The field is left for the owner instead.
        if is_ant_dropdown(loc):
            strict = is_ant_single_select(loc)
            if resolve_ant_dropdown(self.tab(page), loc, value, pick=lambda labels: self._pick_label(labels, value)):
                self._choice_methods[control.ref] = "ant_dropdown"
                return True
            if strict:
                logger.info("No choice matching %r for %r in its list -- left for you", value[:40],
                            control.question[:50])
                return False

        before = {ref for ref, _text in choices_in(self.snapshot(page), under=control.question, any_list=True)}
        opened = False
        try:
            loc.evaluate("e => e.scrollIntoView({block: 'center'})")
        except Exception:
            pass

        # In Ant Design and custom UI widgets, dispatch mousedown on the selector wrapper.
        # Ant Design's rc-select opens specifically on mousedown, not click.
        try:
            loc.evaluate("""e => {
                const box = e.closest('.ant-select-selector, [class*=select-selector], .ant-select, [role=combobox]') || e.parentElement;
                for (const type of ['mousedown', 'mouseup', 'click']) {
                    box.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true}));
                }
                e.focus();
            }""")
            opened = True
        except Exception:
            opened = False

        if not opened:
            try:
                wrapper = loc.evaluate_handle(
                    "el => el.closest('.ant-select-selector, .ant-select, [class*=select-selector], [role=combobox]') || el"
                )
                wrapper.as_element().click(timeout=2_000)
                opened = True
            except Exception:
                try:
                    loc.click(timeout=2_000)
                    opened = True
                except Exception:
                    pass

        def _check_portal(val: str) -> bool:
            for selector in (
                ".ant-select-dropdown:not(.ant-select-dropdown-hidden) [class*=ant-select-item-option]:visible",
                ".ant-select-dropdown:not(.ant-select-dropdown-hidden) [role=option]:visible",
                "[class*=ant-select-item-option]:visible",
                "[class*=select-item-option]:visible",
                "[class*=select__option]:visible",
                "[data-automation-id='promptOption']",
                "[role=listbox]:visible [role=option]:visible",
                "[role=listbox]:visible li:visible",
            ):
                try:
                    portal_opts = tab.locator(selector)
                    count = portal_opts.count()
                    if count > 0:
                        labels = []
                        for i in range(min(count, 80)):
                            try:
                                labels.append(" ".join((portal_opts.nth(i).inner_text(timeout=300) or "").split()))
                            except Exception:
                                labels.append("")
                        if any(labels):
                            pick = self.assistant._best_option(labels, [val])
                            if pick is None:
                                pick = closest_choice(labels, val)
                            if pick is not None:
                                try:
                                    portal_opts.nth(pick).click(timeout=3_000)
                                except Exception:
                                    try:
                                        portal_opts.nth(pick).click(force=True, timeout=2_000)
                                    except Exception:
                                        portal_opts.nth(pick).evaluate("e => e.click()")
                                tab.wait_for_timeout(500)
                                self._choice_methods[control.ref] = "portal_option"
                                return True
                except Exception:
                    pass
            return False

        if _check_portal(value):
            return True

        # Try ArrowDown to trigger dropdown open if portal is not yet shown
        try:
            loc.press("ArrowDown")
            tab.wait_for_timeout(400)
            if _check_portal(value):
                return True
        except Exception:
            pass

        # A long list takes a moment to appear: read at 0.7s, it was empty, and
        # School, Discipline and Veteran Status were all left blank.
        offered = [(ref, text) for ref, text in self._wait_for_choices(page, seconds=4.0, under=control.question,
                                                                       any_list=True) if ref not in before]
        index = self._pick(offered, value)
        if index is None:
            self._dump_open_menu(page, control)
            if _check_portal(value):
                return True

            # Workday renders its options as [data-automation-id='promptOption']
            # elements that often carry no ARIA 'option' role, so neither the
            # snapshot scan nor get_by_role finds them.
            try:
                prompts = tab.locator("[data-automation-id='promptOption']")
                labels = []
                for i in range(min(prompts.count(), 60)):
                    try:
                        labels.append(" ".join((prompts.nth(i).inner_text(timeout=500) or "").split()))
                    except Exception:
                        labels.append("")
                pick = self.assistant._best_option(labels, [value]) if any(labels) else None
                if pick is None and any(labels):
                    pick = closest_choice(labels, value)
                if pick is not None:
                    prompts.nth(pick).click(timeout=5_000)
                    tab.wait_for_timeout(500)
                    self._choice_methods[control.ref] = "prompt_option"
                    return True
            except Exception:
                pass
            # Workday can render its opened menu outside the question's
            # accessibility subtree, so the snapshot scan sees no options.
            # Pick a matching visible menu item directly from the page.
            try:
                wanted = " ".join(value.split()).casefold()
                for role in ("option", "menuitem", "menuitemradio"):
                    for candidate in tab.get_by_role(role).all():
                        label = " ".join((candidate.inner_text(timeout=500) or "").split()).casefold()
                        if label == wanted:
                            candidate.click(timeout=5_000)
                            tab.wait_for_timeout(500)
                            self._choice_methods[control.ref] = "visible_menu"
                            return True
            except Exception:
                pass
            # A type-ahead list shows its choices once something is typed, and a
            # long list needs narrowing down.
            search_terms = self._search_terms(value)
            if re.search(r"address line 1|street address|mailing address", control.question, re.IGNORECASE):
                city = str(getattr(self.profile, "city", "") or "").strip()
                state = str(getattr(self.profile, "state", "") or "").strip()
                postal = str(getattr(self.profile, "postal_code", "") or "").strip()
                full_address = ", ".join(part for part in (value, city, state) if part)
                if postal:
                    full_address = f"{full_address} {postal}".strip()
                if full_address and full_address not in search_terms:
                    search_terms.insert(0, full_address)
            # For state/province fields, prepend the two-letter abbreviation
            # (e.g. "VA" for "Virginia") so type-ahead dropdowns that only show
            # abbreviated options (like Dayforce) match on the first try.
            abbrev = ""
            if re.search(r"\b(state|province)\b", control.question, re.IGNORECASE) and \
                    not re.search(r"education|school|employer|company", control.question, re.IGNORECASE):
                abbrev = _STATE_CODES.get(value.strip().casefold(), "")
                if abbrev and abbrev.upper() not in search_terms:
                    search_terms.insert(0, abbrev.upper())
            for typed in search_terms[:4]:
                try:
                    # Typed, not set: a list that searches as you type only
                    # answers to real keystrokes.
                    typing = self.locate(page, control.ref)
                    try:
                        typing.evaluate("e => { e.scrollIntoView({block: 'center'}); e.focus(); }")
                    except Exception:
                        pass
                    try:
                        typing.click(timeout=1_500)
                    except Exception:
                        pass
                    if control.role in ("combobox", "searchbox", "textbox"):
                        try:
                            typing.fill("", timeout=1_500)
                        except Exception:
                            pass
                    tab.keyboard.type(typed, delay=40)
                    tab.wait_for_timeout(400)
                except Exception:
                    break
                if _check_portal(value) or (abbrev and _check_portal(abbrev)):
                    return True
                offered = [c for c in self._wait_for_choices(page, seconds=2.0, under=control.question, any_list=True)
                           if c[0] not in before]
                index = self._pick(offered, value)
                if index is not None:
                    break
        if index is None and offered:
            # The site's list simply does not have it (Greenhouse's school list
            # has no "Jawaharlal Nehru Technological University"): "Other",
            # where the list offers it, says so honestly.
            other = next((i for i, (_ref, text) in enumerate(offered)
                          if re.fullmatch(r"other( \(please specify\))?", text.strip(), re.IGNORECASE)), None)
            if other is not None:
                self.notes.append(f"{control.question[:70]}: {value[:50]!r} is not on this form's list, "
                                  f"so the agent chose 'Other'")
                logger.info("CHOICE: %r is not offered for %r -- taking 'Other'", value[:40], control.question[:40])
                index = other
        if index is None:
            if re.search(r"\b(state|province)\b", control.question, re.IGNORECASE):
                candidates = [value]
                state_code = geo_reference.us_state_code(value)
                if state_code and state_code.casefold() != value.casefold():
                    candidates.append(state_code)
                for typed in candidates:
                    try:
                        loc.evaluate("e => { e.scrollIntoView({block: 'center'}); e.focus(); }")
                        try:
                            loc.click(timeout=1_500)
                        except Exception:
                            pass
                        loc.press("Control+A")
                        loc.press_sequentially(typed, delay=50)
                        tab.wait_for_timeout(400)
                        if _check_portal(typed) or _check_portal(value):
                            return True
                        # The row that is the answer is clicked; never ArrowDown + Enter (form_fields.pick_open_row).
                        form_fields.pick_open_row(tab, value)
                        tab.wait_for_timeout(500)
                        current = next((c for c in parse_snapshot(self.snapshot(page))
                                        if c.ref == control.ref or _same_question(c.question, control.question)), None)
                        if current is not None and (_same_answer(current.answer, value) or
                                                    _same_answer(current.answer, typed)):
                            logger.info("Committed state/province %r for %r", typed, control.question[:50])
                            self._choice_methods[control.ref] = "type_and_commit"
                            return True
                    except Exception:
                        continue
            if re.search(r"address line 1|street address|mailing address", control.question, re.IGNORECASE):
                try:
                    loc.click(timeout=4_000)
                    loc.press("Control+A")
                    loc.press_sequentially(value, delay=45)
                    loc.press("Tab")
                    tab.wait_for_timeout(700)
                    current = next((c for c in parse_snapshot(self.snapshot(page))
                                    if _same_question(c.question, control.question)), None)
                    if current is not None and _same_answer(current.answer, value):
                        logger.info("Accepted typed address %r without an exact autocomplete match", value[:60])
                        self._choice_methods[control.ref] = "typed_value"
                        return True
                except Exception:
                    pass
            # Some widgets never put their list where the page's structure can
            # be read (R+L's "Employer State or Province"). A person types and
            # presses Down then Enter; so does the agent, and then checks the
            # form has stopped calling the field empty.
            if self.type_and_commit(page, control, value):
                self._choice_methods[control.ref] = "type_and_commit"
                return True
            tab.keyboard.press("Escape")
            logger.info("No choice matching %r for %r among %s", value, control.question[:50],
                        [t for _r, t in offered][:8])
            return False
        self.locate(page, offered[index][0]).click(timeout=5_000)
        tab.wait_for_timeout(500)
        self._choice_methods[control.ref] = "list_option"
        return True

    def _dump_open_menu(self, page, control) -> None:
        """Diagnostic: capture the visible custom-widget DOM of an opened
        dropdown whose options could not be read, so its structure can be
        inspected and driven. Runs at most once per run."""
        if getattr(self, "_menu_dumped", False):
            return
        self._menu_dumped = True
        tab = self.tab(page)
        try:
            info = tab.evaluate(r"""() => {
                const vis = e => !!(e.offsetParent || e.getClientRects().length);
                const short = s => (s || '').replace(/\s+/g, ' ').trim().slice(0, 90);
                const out = [];
                const seen = new Set();
                for (const e of document.querySelectorAll('[data-automation-id], [role], li, [class*=popup i], [class*=menu i], [class*=option i]')) {
                    if (!vis(e)) continue;
                    const role = e.getAttribute('role') || '';
                    const aid = e.getAttribute('data-automation-id') || '';
                    const cls = (e.className && e.className.toString) ? e.className.toString().slice(0, 60) : '';
                    if (!aid && !['option','listbox','menu','menuitem','listitem','combobox','button'].includes(role)
                        && !/popup|menu|option|dropdown/i.test(cls)) continue;
                    const key = e.tagName + '|' + role + '|' + aid + '|' + short(e.textContent);
                    if (seen.has(key)) continue;
                    seen.add(key);
                    out.push(`${e.tagName.toLowerCase()} role='${role}' aid='${aid}' cls='${cls}' :: ${short(e.textContent)}`);
                }
                return out.slice(0, 150).join('\n');
            }""")
            folder = self.job_dir if self.job_dir else Path("output")
            path = Path(folder) / f"dropdown_dump_{control.ref}.txt"
            path.write_text(f"question={control.question!r}\nurl={tab.url}\n\n{info}\n", encoding="utf-8")
            logger.info("DROPDOWN_DUMP written to %s", path)
        except Exception as exc:
            logger.info("DROPDOWN_DUMP failed: %s", str(exc).splitlines()[0][:120])

    def put_in_a_spinbutton(self, page, loc, value: str) -> bool:
        """Get a value into a box that keeps its own count, and check it took."""
        tab = self.tab(page)

        def holds_it() -> bool:
            try:
                return (loc.input_value(timeout=2_000) or "").strip().lstrip("0") == value.strip().lstrip("0")
            except Exception:
                return False

        def by_setting():
            fill_and_dispatch(loc, value, timeout=5_000)

        def by_typing():
            loc.focus(timeout=3_000)
            tab.keyboard.press("Control+a")
            tab.keyboard.type(value, delay=80)

        def by_clearing_then_typing():
            loc.fill("", timeout=3_000)
            loc.press_sequentially(value, delay=80, timeout=6_000)

        for attempt in (by_setting, by_typing, by_clearing_then_typing):
            try:
                attempt()
                tab.wait_for_timeout(300)
            except Exception as exc:
                logger.debug("%s did not work: %s", attempt.__name__, str(exc).splitlines()[0][:70])
                continue
            if holds_it():
                return True
        return holds_it()

    def type_and_commit(self, page, control: Control, value: str) -> bool:
        """Type the answer into a list box and take its first suggestion.

        True only when the form stops saying the field is empty -- typing alone
        leaves a value showing that the form does not accept.
        """
        tab = self.tab(page)
        complaint = re.compile(r"The " + re.escape(control.question) + r"[^\n]*? is required", re.IGNORECASE)
        for typed in self._search_terms(value):
            try:
                box = self.locate(page, control.ref)
                box.click(timeout=4_000)
                box.fill("", timeout=3_000)
                tab.keyboard.type(typed, delay=60)
                tab.wait_for_timeout(1_500)          # the list is fetched as you type
                # The row that is the answer is clicked; never ArrowDown + Enter, which took the first row and, with
                # no list open, sent the whole form (form_fields.pick_open_row).
                form_fields.pick_open_row(tab, value)
                tab.wait_for_timeout(1_200)
                # Leave the box before judging it: text that was typed but never
                # chosen stays in the box until then, and was read as the answer.
                tab.keyboard.press("Tab")
                tab.wait_for_timeout(600)
            except Exception as exc:
                logger.debug("Could not type %r into %r: %s", typed, control.question[:40],
                             str(exc).splitlines()[0][:80])
                return False
            snapshot = self.snapshot(page)
            shown = next((c for c in parse_snapshot(snapshot)
                          if _same_question(c.question, control.question)), None)
            took = shown is not None and shown.answer and _same_answer(shown.answer, value)
            if took and not complaint.search(snapshot):
                logger.info("CHOSE %r for %r by typing and clicking its row", value[:30], control.question[:40])
                return True
            if shown is not None and shown.answer and not took:
                # Enter took whichever row was showing, and it is not what was asked
                # for ("Springfield Gardens" for "Springfield"): a wrong answer is worse than
                # none, so it is taken out again.
                try:
                    box = self.locate(page, control.ref)
                    box.click(timeout=2_000)
                    box.press("Control+A")
                    for _ in range(2):
                        box.press("Backspace")
                    tab.wait_for_timeout(400)
                    logger.info("REMOVED: %r was not %r for %r -- left empty", shown.answer[:50], value[:30],
                                control.question[:40])
                except Exception as exc:
                    logger.debug("Could not remove a wrong choice: %s", str(exc).splitlines()[0][:80])
        return False

    def _wait_for_choices(self, page, seconds: float = 8.0, under: str = "",
                          any_list: bool = False) -> list[tuple[str, str]]:
        """The choices a list is showing, once it has finished showing them."""
        tab = self.tab(page)
        deadline = time.time() + seconds
        offered: list[tuple[str, str]] = []
        while time.time() < deadline:
            tab.wait_for_timeout(500)
            offered = choices_in(self.snapshot(page), under=under, any_list=any_list)
            if offered:
                break
        return offered

    MAX_PEEKS_PER_READ = 6        # dropdowns opened in one look at a page: each costs a moment
    MAX_CHOICES_GIVEN = 30        # a longer list (Schools, Countries) is typed into, not handed to the planner

    def read_hidden_choices(self, page, controls: list[Control], snapshot: str,
                            only: Optional[list[str]] = None) -> str:
        """The choices of blank dropdowns that draw their list only when opened,
        as words for the planner ("" when there is none to read).

        ADP's required "If you are under 18 years of age, can you provide proof...?"
        reaches the agent as `button "Choose an option"` with nothing under it.
        Claude, asked to answer without the choices, said they were unknown and the
        question went back to the owner. Opening the list shows them; the list is
        closed again and nothing is chosen here -- choosing stays with the planner's
        answer and `choose()`.

        Praxis's two required dropdowns are named by their question, not by a prompt,
        so they were never opened, and Claude answered "No" and "Yes" to lists that
        say "Never Employed by Praxis" and "I have read, authorize and acknowledge".
        A blank dropdown that names its own question is opened too when it is one
        the agent could not answer itself (`only`: the questions it could not).
        """
        notes: list[str] = []
        peeked_now = 0
        for control in controls:
            if control.role not in ("button", "combobox", "listbox") or control.disabled or control.options \
                    or control.answer:
                continue
            if not _PROMPT_NAME.match((control.name or control.value or "").strip()):
                # Named by its question: only a dropdown (never a button) the agent could not answer.
                if control.role == "button" or not only \
                        or not any(_same_question(control.question, q) for q in only):
                    continue
                try:
                    if is_ant_dropdown(self.locate(page, control.ref)):
                        continue                  # read whole by its own reader (interaction.resolve_ant_dropdown)
                except Exception:
                    continue
            label = label_above(snapshot, control.ref) or control.context or control.container
            if not _PROMPT_NAME.match((control.name or control.value or "").strip()):
                label = control.question
            key = f"{self._current_host}|{(label or control.ref)[:80]}"
            if self._peeked.get(key, 0) >= 2 or len(notes) >= 4 or peeked_now >= self.MAX_PEEKS_PER_READ:
                continue
            self._peeked[key] = self._peeked.get(key, 0) + 1
            peeked_now += 1
            choices = self._open_and_read(page, control, label)
            if len(choices) > self.MAX_CHOICES_GIVEN:
                continue
            if choices:
                logger.info("CHOICES: read %d from %r by opening it", len(choices), (label or control.name)[:60])
                notes.append(f"the choices for {label or control.name!r} (the {control.name!r} control, ref {control.ref}) "
                             f"are: " + " | ".join(f"'{c}'" for c in choices)
                             + " -- answer it with one of these if the facts settle it")
        return "; ".join(notes)

    def _open_and_read(self, page, control: Control, label: str) -> list[str]:
        """Opens one dropdown, reads what it offers, and closes it again."""
        tab = self.tab(page)
        try:
            loc = self.locate(page, control.ref)
            showing = {ref for ref, _t in choices_in(self.snapshot(page), under=label, any_list=True)}
            if not self.assistant._click_resiliently(loc, timeout_ms=4_000):
                return []
            offered = [text for ref, text in self._wait_for_choices(page, seconds=4.0, under=label, any_list=True)
                       if ref not in showing]
            # Close it: Escape, and where the widget ignores that, the control that opened it.
            for close in (lambda: tab.keyboard.press("Escape"), lambda: loc.click(timeout=2_000)):
                if not [1 for ref, _t in choices_in(self.snapshot(page), under=label, any_list=True)
                        if ref not in showing]:
                    break
                try:
                    close()
                except Exception:
                    pass
                tab.wait_for_timeout(400)
            return list(dict.fromkeys(offered))
        except Exception as exc:
            logger.debug("Could not read the choices of %r: %s", (label or control.name)[:50],
                         str(exc).splitlines()[0][:100])
            return []

    def _pick(self, offered: list[tuple[str, str]], value: str) -> Optional[int]:
        """Which choice to take: the one that matches, else the closest."""
        names = [text for _ref, text in offered]
        if not names:
            return None
        # A dialling code ("+1") is offered by several countries: take the one
        # the profile names. With no country in the profile, nothing is assumed.
        if re.fullmatch(r"\+?\d{1,4}", value.strip()):
            code = value.strip() if value.strip().startswith("+") else f"+{value.strip()}"
            country = geo_reference.country_code(str(getattr(self.profile, "country", "") or ""))
            for i, name in enumerate(names):
                if country and (code in name or value.strip() in name) and geo_reference.country_code(name) == country:
                    logger.info("Matched dial code %r to profile country choice: %r", value, name)
                    return i
        index = self.assistant._best_option(names, [value])
        if index is None:
            index = closest_choice(names, value)
            if index is not None:
                logger.info("Closest choice to %r is %r", value[:40], names[index][:60])
        return index

    def _pick_label(self, labels: list[str], value: str) -> Optional[int]:
        """Which of a list's labels answers `value`. A country or US state is
        that place in any spelling or nothing ("United States" is never "United
        States Minor Outlying Islands", a first row, or a blank); anything else
        follows the page agent's usual rules (_pick)."""
        if not str(value or "").strip():
            return None
        if geo_reference.country_code(value) or geo_reference.us_state_code(value):
            return next((i for i, label in enumerate(labels)
                         if label.strip() and (geo_reference.same_place(label, value)
                                               or geo_reference.normalize(label) == geo_reference.normalize(value))),
                        None)
        index = self._pick([(str(i), label) for i, label in enumerate(labels)], value)
        return index if index is not None and labels[index].strip() else None

    @staticmethod
    def _search_terms(value: str) -> list[str]:
        """What to type into a list that searches: the answer, then less of it."""
        words = [w for w in re.split(r"[^\w&]+", value) if w]
        terms = [value]
        if len(words) > 3:
            terms.append(" ".join(words[:3]))
        if len(words) > 1:
            terms.append(words[0])
        return terms[:3]

    def _page_controls(self, page) -> list[Control]:
        try:
            return parse_snapshot(self.snapshot(page))
        except Exception:
            return []

    def _choose_inside(self, page, control: Control, value: str) -> bool:
        """Clicks the choice inside a group -- by what it says, since it has no
        reference of its own."""
        index = self.assistant._best_option(control.options, [value])
        if index is None:
            index = closest_choice(control.options, value)
        if index is None:
            logger.info("No choice matching %r for %r among %s", value, control.question[:50], control.options[:6])
            return False
        wanted = control.options[index]
        inside = self.locate(page, control.ref)
        exact = re.compile(rf"^\s*{re.escape(wanted)}\s*$", re.IGNORECASE)
        for role in ("radio", "checkbox", "button", "option", "link"):
            try:
                choice = inside.get_by_role(role, name=exact)
                if choice.count() and choice.first.is_visible():
                    try:
                        choice.first.check(timeout=4_000)
                    except Exception:
                        choice.first.click(timeout=4_000)
                    logger.info("Chose %r inside %r", wanted[:40], control.question[:40])
                    return True
            except Exception:
                continue
        try:
            fallback = inside.get_by_text(exact).first
            if fallback.count():
                fallback.click(timeout=4_000)
                return True
        except Exception:
            pass
        return False

    def _letter_file(self) -> Optional[Path]:
        if self._letter is None and self.cover_letter is not None:
            self._letter = self.cover_letter()
        return self._letter[1] if self._letter else None

    def _learning_host(self) -> str:
        return (self._current_host or urlparse(getattr(self.job, "url", "")).netloc).lower()

    def _recipe_identity(self, control: Control, answer: Answer) -> Optional[tuple[str, str, str, str, str]]:
        question = control.question or answer.question
        if answer.action != "choose" or control.role not in ("textbox", "searchbox", "combobox", "listbox", "spinbutton") \
                or not _learnable_recipe_question(question):
            return None
        host = self._learning_host()
        if not host:
            return None
        return (
            host,
            _recipe_question_key(question),
            control.role,
            _recipe_options_signature(control.options),
            answer.action,
        )

    def _recalled_form_recipe(self, control: Control, answer: Answer) -> Optional[dict]:
        identity = self._recipe_identity(control, answer)
        if identity is None or self.tracker is None or not hasattr(self.tracker, "recall_form_recipe"):
            return None
        try:
            return self.tracker.recall_form_recipe(*identity)
        except Exception as exc:
            logger.debug("Could not recall a form recipe: %s", str(exc).splitlines()[0][:100])
            return None

    def _mark_recipe_failed(self, identity: tuple[str, str, str, str, str]) -> None:
        if self.tracker is None or not hasattr(self.tracker, "mark_form_recipe_failed"):
            return
        try:
            self.tracker.mark_form_recipe_failed(*identity)
        except Exception as exc:
            logger.debug("Could not mark a form recipe failed: %s", str(exc).splitlines()[0][:100])

    @staticmethod
    def _answer_verification_state(answer: Answer, before: Control, after: list[Control]) -> Optional[bool]:
        """True only when the snapshot visibly confirms an answer persisted."""
        if answer.action in ("upload_resume", "upload_cover_letter"):
            return None
        by_ref = {control.ref: control for control in after}
        by_question: dict[str, list[Control]] = {}
        for control in after:
            by_question.setdefault(control.question, []).append(control)
        now = by_ref.get(before.ref) or next(iter(by_question.get(before.question, [])), None)
        if now is None:
            return None
        if answer.action == "choose" and before.role in ("radio", "checkbox", "switch"):
            group = [control for control in after
                     if control.role == before.role and before.group and control.group == before.group]
            return any(control.checked and _same_answer(control.name, answer.value) for control in group) or \
                (not group and now.checked)
        if answer.action in ("check", "uncheck"):
            if now.role == "radio" and now.group:
                checked = any(control.checked for control in by_question.get(now.group, []) if control.ref == before.ref) \
                    or now.checked
            else:
                checked = now.checked
            return checked if answer.action == "check" else not checked
        shown = now.answer.strip().lower()
        wanted = answer.value.strip().lower()
        digits = re.sub(r"\D", "", wanted)
        return bool(shown) and (wanted in shown or shown in wanted or _same_answer(shown, wanted)
                                or (len(digits) >= 7 and digits[-10:] in re.sub(r"\D", "", shown)))

    def _commit_learned_memory(self, after: list[Control]) -> None:
        """Makes a recipe durable only after a later snapshot proves it worked."""
        pending, self._pending_memories = self._pending_memories, []
        for item in pending:
            answer, control = item["answer"], item["control"]
            verified = self._answer_verification_state(answer, control, after)
            identity = item.get("recipe")
            if verified is not True:
                if verified is False and item.get("reused_recipe") and identity is not None:
                    self._mark_recipe_failed(identity)
                continue
            if identity is not None and self.tracker is not None and hasattr(self.tracker, "record_form_recipe"):
                try:
                    self.tracker.record_form_recipe(*identity, item["method"])
                except Exception as exc:
                    logger.debug("Could not save a verified form recipe: %s", str(exc).splitlines()[0][:100])
            if item.get("record_answer") and self.tracker is not None and hasattr(self.tracker, "record_answer"):
                try:
                    self.tracker.record_answer(
                        self.key, self._learning_host(), control.question, answer.value,
                        options=control.options or None, answered_by="verified_agent",
                    )
                except Exception as exc:
                    logger.debug("Could not record the verified answer: %s", str(exc).splitlines()[0][:100])

    def _remember(self, control: Control, answer: Answer) -> None:
        question = control.question or answer.question
        if not _learnable_recipe_question(question):
            return
        recipe = self._recipe_identity(control, answer)
        record_answer = bool(
            self.tracker is not None and hasattr(self.tracker, "record_answer") and self.key
            and answer.action in ("fill", "choose", "check")
            and not answer.source.startswith("profile.")
            and answer.source != "owner_earlier_answer"
        )
        if recipe is None and not record_answer:
            return
        self._pending_memories.append({
            "answer": answer,
            "control": control,
            "method": self._choice_methods.pop(control.ref, "default"),
            "recipe": recipe,
            "reused_recipe": self._reused_recipes.pop(control.ref, None) is not None,
            "record_answer": record_answer,
        })

    # -- what stops the run -----------------------------------------------------------
    def record_unanswered(self, question: str, reason: str, controls: list[Control]) -> None:
        """Keeps a required question the agent had to leave for the owner in
        data/unanswered_questions.json: one entry per question, with why it was left,
        the choices the form offered, where it was met, and the line that answers it
        (the entry's "answer_key", for data/profile_answers.json). An entry shows its
        answer once the library has one. Never stops the run: the file is a record."""
        try:
            path = Path(getattr(self, "unanswered_path", UNANSWERED_FILE))
            clean = " ".join(re.sub(r"\s*\*\s*$", "", question or "").split())
            key = _plain(clean)[:160]
            if not key:
                return
            site = getattr(self, "_current_host", "") or ""
            company = str(getattr(getattr(self, "job", None), "company", "") or "")
            counted = getattr(self, "_logged_unanswered", None)
            if counted is None:
                counted = self._logged_unanswered = set()
            first_time_this_run = (key, site) not in counted
            counted.add((key, site))
            try:
                data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            except (OSError, ValueError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            entry = data.get(key)
            if not isinstance(entry, dict):
                entry = {"question": clean, "required": True, "first_seen": date.today().isoformat(),
                         "times": 0, "sites": [], "companies": [], "options": []}
            control = next((c for c in controls if _same_question(c.question, question)), None)
            choices = [o for o in (control.options if control else []) if o.strip() and not PLACEHOLDER.match(o)]
            if first_time_this_run:
                entry["times"] = int(entry.get("times") or 0) + 1
            entry["last_seen"] = date.today().isoformat()
            entry["reason"] = safety.redact(reason or "")[:300]
            for field_name, value in (("sites", site), ("companies", company)):
                if value and value not in entry.setdefault(field_name, []):
                    entry[field_name].append(value)
            for option in choices:
                if option not in entry.setdefault("options", []):
                    entry["options"].append(option)
            words = _plain(clean).split()[:10]
            entry["answer_key"] = "re:" + r"\s+".join(re.escape(w) for w in words)
            data[key] = entry
            # What the owner has answered since shows beside the question.
            for held in data.values():
                if isinstance(held, dict) and held.get("question"):
                    held["answer"] = self.library_answer(str(held["question"])) or None
            path.parent.mkdir(parents=True, exist_ok=True)
            scratch = path.with_name(path.name + ".tmp")
            scratch.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            scratch.replace(path)
        except Exception as exc:
            logger.debug("Could not record an unanswered question: %s", str(exc).splitlines()[0][:100])

    def blockers(self, page, plan: PagePlan, controls: list[Control], about_to_send: bool = True) -> list[str]:
        """What must stop the run. Before a submit that includes a required
        question nobody has answered; mid-form it does not -- R+L asks "have
        you added 3 consecutive years of work history?", which can only be
        answered once the agent has pressed Add Experience and filled it in.
        """
        reasons: list[str] = []
        if safety.captcha_visible(page):
            reasons.append("a CAPTCHA is showing -- only you can complete it")
        for conflict in safety.legal_answer_conflicts(answered_fields(controls), self.profile):
            reasons.append(f"sponsorship/work authorization doesn't match your profile: {conflict}")
        # What Claude noticed on the page, including text on a review page that
        # is not a form control: a legal answer that disagrees with the profile
        # stops the run; anything else is passed on to the owner.
        for mismatch in plan.mismatches:
            question = " ".join(str(mismatch.get("question") or "").split())
            said = str(mismatch.get("on_page") or "")[:60]
            if any(_same_question(question, q) for q in self.corrected):
                continue   # already put right from the profile, and checked since
            else:
                # What Claude reads as a mismatch is passed on, not acted on:
                # R+L's "legally eligible ... on an ongoing indefinite basis?"
                # says Yes, which is what the profile says, and the run was
                # held up over it. Only the agent's own check above, made on
                # what the page actually holds, stops an application.
                note = f"{question[:80]}: the page says {said!r}, your profile says {str(mismatch.get('facts_say') or '')[:60]!r}"
                if note not in self.notes:
                    self.notes.append(note)
        try:
            snapshot = self.snapshot(page) or ""
        except Exception:
            snapshot = ""
        for item in plan.for_owner:
            question = str(item.get("question") or "")
            # Whether this field needs the owner is the field's own requirement (its section, its label, the
            # planner's word), decided in field_requirements. A password in an optional account section stays
            # empty (Meta, 30 September); a required one elsewhere on the same page still stops the run.
            req = field_requirements.requirement_for(snapshot, question, str(item.get("ref") or ""),
                                                     bool(item.get("required")))
            still_blank = not any(c.question == question and c.answer for c in controls)
            if req.status in (field_requirements.OPTIONAL, field_requirements.CONDITIONAL):
                logger.info("NOT NEEDED: %s -- %s", question[:70], req.evidence)
                continue
            if field_requirements.needs_owner(req, not still_blank):
                note = f"needs your answer: {question[:90]} ({item.get('reason', '')})"
                self.record_unanswered(question, str(item.get("reason", "")), controls)
                if about_to_send:
                    reasons.append(note)
                elif note not in self.notes:
                    self.notes.append(note)
                    logger.info("LEFT FOR YOU (for now): %s", note[:140])
        try:
            pending = self.assistant.pending_attestations(self.tab(page))
        except Exception:
            pending = []
        if pending and plan.next_kind == "final_submit":
            reasons += [f"your declaration or signature: {p[:90]}" for p in pending]
        return reasons

    # -- moving on ----------------------------------------------------------------------
    def _in_site_menu(self, page, control: Control) -> bool:
        """Whether a button sits in the site's header, navigation or footer rather than in the page's content."""
        try:
            return bool(self.locate(page, control.ref).evaluate(
                "e => !!e.closest('header, nav, footer, [role=banner], [role=navigation], [role=contentinfo]')",
                timeout=2_000))
        except Exception:
            return False

    def press_next(self, page, plan: PagePlan, controls: list[Control]) -> tuple[str, object, str]:
        """("moved" | "submitted" | "retry" | "stop", page, what happened)."""
        tab = self.tab(page)

        # Pre-navigation sweep: scan DOM for inline resume-parsed experience or
        # education cards stuck in active 'Edit' or 'Draft' modes. Autonomously
        # click internal card buttons ('Update', 'Save Entry', 'Done', etc.) and
        # wait for network settlement before evaluating the final 'Next' button.
        try:
            committed = commit_draft_cards(tab)
            if committed:
                logger.info("PRE-NAVIGATION: committed %d active draft card(s)", committed)
                self.settle(page, 1_500)
                controls = parse_snapshot(self.snapshot(page))
        except Exception as exc:
            logger.debug("Pre-navigation card commit sweep encountered an issue: %s", exc)

        by_ref = {c.ref: c for c in controls}
        control = by_ref.get(plan.next_ref)
        if plan.next_kind == "none" or control is None:
            # Nothing to press may only mean the page is still arriving: read it
            # again (the same page counts towards the tries) before giving up.
            self.settle(page, 3_000)
            return "retry", page, "no way forward was found on this page"
        label = " ".join((control.name or plan.next_label).split())
        if plan.page_kind == "application_form":
            self._seen_form = True
        # The site's own menu (its header, navigation or footer) is never a step of an application already under
        # way. UKG, 30 September: the page fell back to the job board's home mid-application, the plan pressed the
        # header's "Find Opportunities", searched the board and opened another job's link. Once the form has been
        # seen, a page that offers only the menu is left for the job's own page, where the application is resumed.
        if getattr(self, "_seen_form", False) and plan.next_kind != "open_application" \
                and not re.search(r"\bapply\b", label, re.IGNORECASE) and self._in_site_menu(page, control):
            back = str(getattr(self.job, "url", "") or "")
            if not back:
                return "stop", page, f"{label!r} is the site's own menu -- the page has left the application"
            logger.info("NOT PRESSING %r: the site's own menu, not a step of the application -- back to the job", label)
            tab.goto(back, wait_until="domcontentloaded", timeout=45_000)
            self.settle(page, 2_000)
            return "moved", page, "back to the job's own page"
        # "Add Education", "Add Experience" add an empty entry -- a way forward only while the owner has more
        # degrees or jobs than the page shows. UKG, 30 September: the plan pressed "Add Education" as its next step
        # on a page already showing both degrees, and the new entry was filled with the master's a second time.
        adding = _ADD_ENTRY.match(label)
        if adding:
            section = "education" if re.search(r"educat|degree|school", adding.group(1), re.IGNORECASE) else "work"
            shown = len({e.index for e in getattr(self, "_entries", {}).values() if e.section == section})
            owned = len((getattr(self, "history", {}) or {}).get("education" if section == "education"
                                                                  else "experience") or [])
            if shown >= owned:
                onward = self.profile_forward([c for c in controls if not _ADD_ENTRY.match(" ".join((c.name or "").split()))])
                logger.info("NOT PRESSING %r: the page already shows %d of your %d -- %s", label, shown, owned,
                            f"pressing {onward.name!r} instead" if onward else "looking for the way forward")
                if onward is None:
                    return "retry", page, f"{label!r} only adds an empty entry -- it is not the way forward"
                control, label = onward, " ".join((onward.name or "").split())
        if control.disabled:
            return "retry", page, f"{label!r} is not enabled yet"
        if self.GOOGLE_SIGN_IN.search(label) and host_of(tab.url) in self._google_failed:
            # NVIDIA answered "Account is Inactive" to this Google account. Its
            # own sign-in is taken instead, whoever proposed the Google one.
            instead = next((c for c in controls if c.role in PRESS_ROLES and re.search(
                r"sign ?in with email|use email|continue with email|create (an )?account|sign up|register",
                c.name or "", re.IGNORECASE)), None)
            if instead is None:
                return "stop", page, ("this site will not accept the Google account and offers no other way "
                                      "to sign in")
            logger.info("NEXT: Google was refused here -- using %r instead", (instead.name or "")[:40])
            control = instead
            label = " ".join((control.name or "").split())
        if NEVER_PRESS.search(label):
            return "stop", page, f"the way forward looked like {label!r}, which the agent never presses"
        # "Add Experience" was pressed again and again, leaving empty entries
        # behind, because the entry could not be filled in.
        pressed = self._pressed.get(label.lower(), 0)
        # An entry form open on the page has its own confirm button, named like
        # the one that opened it ("Add Experience" beside "Cancel"): pressing
        # that saves the entry, and is never the loop this count is for.
        confirming = any(c.role in PRESS_ROLES and re.fullmatch(r"\s*cancel\s*", c.name or "", re.IGNORECASE)
                         for c in controls)
        if re.match(r"^\s*add\b", label, re.IGNORECASE) and pressed >= 4 and not confirming:
            return "stop", page, (f"{label!r} has been pressed {pressed} times and nothing was filled in "
                                  f"between -- the rest of this section needs you")
        self._pressed[label.lower()] = pressed + 1
        if re.search(r"linked ?in|indeed|facebook|apple|microsoft", label, re.IGNORECASE):
            return "stop", page, f"{label!r} is a sign-in the agent never uses"
        if plan.next_kind == "consent" or re.search(r"\b(i )?(agree|accept|acknowledge|consent)\b", label, re.IGNORECASE):
            if safety.is_attestation(label) or re.search(r"certif|attest|sign", label, re.IGNORECASE):
                return "stop", page, f"{label!r} is a declaration -- only you can give it"
            if not getattr(self.profile, "accept_application_privacy_prompts", False):
                return "stop", page, f"{label!r} accepts a notice -- you haven't allowed the agent to accept those"

        submit_word = safety.is_submit_label(label) and plan.page_kind != "job_description"
        # Schwab's questions page is step 2 of 5 and its button says "Submit":
        # it saves that step. A "Submit" is the application's last only when no
        # step counter shows more steps to come.
        counter = re.search(r"(\d+)\s*(?:of|/)\s*(\d+)", plan.step or "")
        steps_remain = bool(counter) and int(counter.group(1)) < int(counter.group(2))
        is_review = getattr(self.assistant, "is_review_step", lambda p: False)(tab)
        # The review heuristic answers yes whenever a button reads "Submit",
        # so it must not outvote a step counter that shows more steps to come
        # (Schwab's step 2 of 5 is exactly that button).
        final = plan.next_kind == "final_submit" or (is_review and not steps_remain) \
            or (submit_word and not (steps_remain and plan.next_kind == "next_step"))
        if plan.page_kind == "job_description" and plan.next_kind == "open_application":
            final = False
        if final:
            gate = self.submit_gate(page, controls)
            if gate:
                return "stop", page, gate
            logger.info("SUBMITTING: pressing %r -- every check passed", label)
        elif submit_word:
            # Not the last step -- but a button that could send the application
            # still gets every check except the finished-application ones.
            gate = self.submit_gate(page, controls, last_step=False)
            if gate:
                return "stop", page, gate
            logger.info("NEXT: pressing %r (step %s -- more steps follow)", label, plan.step)
        else:
            logger.info("NEXT: pressing %r", label)

        # Task 4.1: Deploy the State Fingerprint Circuit Breaker
        if not hasattr(self, "_circuit_breaker") or self._circuit_breaker is None:
            from state_machine import StateFingerprintCircuitBreaker
            self._circuit_breaker = StateFingerprintCircuitBreaker(consecutive_threshold=3)

        tripped, state, meta = self._circuit_breaker.check(tab)
        if tripped:
            logger.warning("CIRCUIT BREAKER TRIPPED in press_next: %s across 3 consecutive cycles", state)
            dump_dir = self._circuit_breaker.trip_and_dump(
                tab,
                reason="State fingerprint identical across 3 consecutive cycles",
                tracker=getattr(self, "tracker", None),
                key=getattr(self, "key", ""),
                console_logs=getattr(tab, "_console_logs", []),
            )
            # A page that will not move on needs the owner, like every other
            # stop: the run hands the open browser over instead of ending
            # with the application abandoned. The evidence is kept either way.
            # What is still blank is named from the page itself (the field inventory, which groups choices by the
            # page's own names): Paylocity's stop said only "stuck" while eight required questions sat unanswered.
            try:
                blank = [f.question or f.label or f.name
                         for f in form_fields.blank_required(form_fields.inventory(tab))]
            except Exception:
                blank = []
            still = ("; still blank and required: " + " | ".join(q[:90] for q in blank[:10])) if blank else ""
            return "stop", page, (f"the page did not change after three tries -- stuck in a validation loop "
                                  f"({state}); evidence in {dump_dir}{still}")

        before = re.sub(r"\[ref=[\w-]+\]|\[active\]", "", self.snapshot(page))
        tabs_before = len(tab.context.pages)
        if submit_word:
            self.final_pressed = True   # a confirmation after any Submit counts
        pressed_at = time.time()
        if OPENS_A_FILE_DIALOG.search(label) and self.resume_file and Path(self.resume_file).is_file():
            try:
                with tab.expect_file_chooser(timeout=6_000) as chooser:
                    self.locate(page, control.ref).click(timeout=10_000)
                chooser.value.set_files(str(self.resume_file))
                logger.info("FILE DIALOG: %r asked for a file, so the agent gave it the resume", label[:40])
            except Exception:
                pass          # no dialog came; the click itself still happened
        else:
            self.locate(page, control.ref).click(timeout=10_000)
        self.settle(page, 2_500)
        page = self.newest_tab(page, tabs_before)
        try:
            sweep_modals_and_policies(self.tab(page), self.profile)
        except Exception as exc:
            logger.debug("Post-navigation modal sweep: %s", exc)
        if final:
            self.final_pressed = True
            if self._site_confirms(self.tab(page)):
                logger.info("CONFIRMED by the site %.0fs after submitting", time.time() - pressed_at)
                return "submitted", page, ""
        after = re.sub(r"\[ref=[\w-]+\]|\[active\]", "", self.snapshot(page))
        if after == before:
            alerts = [c for c in re.findall(r"- alert[^:\n]*: (.+)", self.snapshot(page))][:5]
            return "retry", page, ("the page did not move on" +
                                   (f"; it says: {'; '.join(alerts)}" if alerts else ""))
        return "moved", page, ""

    def submit_gate(self, page, controls: list[Control], last_step: bool = True) -> str:
        """Why the application must not be sent now, or "" when it may.

        The last Submit is never pressed from here. Only two paths may send an application: the owner, after
        apply_flow.hand_over(), and the verified path (safety.evaluate_auto_submit) that the hand-over runs. This
        gate used to press it itself whenever the old AUTO_SUBMIT setting was on -- a setting apply_flow no longer
        honours -- and on 29 September it pressed Secunetics' 'Submit Application' four times, with State still
        empty and past a CAPTCHA. Every last step now stops here with the words that send it to the hand-over."""
        if last_step:
            # Not a form still without the resume it asks for.
            if self._tailored_resume_missing(page) and self._form_asks_for_a_resume(page, controls):
                return "the tailored resume is not attached"
            return "automatic submission is off -- the application is ready for you to submit"
        # A button that says Submit on a step with more to come: it saves the step, and gets every check but the
        # finished-application ones. The old AUTO_SUBMIT setting plays no part.
        if safety.captcha_visible(page):
            return "a CAPTCHA is showing -- only you can complete it"
        conflicts = safety.legal_answer_conflicts(answered_fields(controls), self.profile)
        if conflicts:
            return f"sponsorship/work authorization doesn't match your profile: {conflicts[0]}"
        try:
            pending = self.assistant.pending_attestations(self.tab(page))
        except Exception:
            pending = []
        if pending:
            return f"your declaration or signature is needed: {pending[0][:90]}"
        if last_step and self._tailored_resume_missing(page):
            return "the tailored resume is not attached"
        return ""

    def _tailored_resume_missing(self, page) -> bool:
        return bool(self.resume_file) and not (
            self.resume_uploaded or getattr(self, "resume_seen", False)
            or self._resume_on_page(page) or self._resume_attached_before())

    def _form_asks_for_a_resume(self, page, controls: list[Control]) -> bool:
        if _upload_control(controls, _RESUME_WORDS) is not None:
            return True
        try:
            return bool(_REQUIRED_RESUME_LABEL.search(self.snapshot(page)))
        except Exception:
            return False

    # -- small helpers --------------------------------------------------------------------
    @staticmethod
    def _password_boxes(tab) -> int:
        """How many password boxes the page shows, frames included."""
        try:
            count = tab.locator("input[type=password]:visible").count()
            for frame in tab.frames[1:]:
                if not safety.is_captcha_frame(frame.url):
                    count += frame.locator("input[type=password]:visible").count()
            return count
        except Exception:
            return 0

    @staticmethod
    def _password_box(tab) -> bool:
        try:
            box = tab.locator("input[type=password]:visible")
            if box.count():
                return True
            return any(f.locator("input[type=password]:visible").count() for f in tab.frames[1:]
                       if not safety.is_captcha_frame(f.url))
        except Exception:
            return False

    def _site_confirms(self, tab) -> bool:
        """True only for a page that says the application was received in a
        confirmation's own words AND asks for nothing more. A page that still shows
        a password box or a form to fill in is not a confirmation, whatever it says."""
        try:
            frames = [f for f in tab.frames[1:]
                      if (f.url or "").startswith("http") and not safety.is_captcha_frame(f.url)]
            texts = [tab.inner_text("body", timeout=5_000)]
            texts += [f.locator("body").inner_text(timeout=3_000) for f in frames]
            if not any(CONFIRMATION_TEXT.search(t or "") for t in texts):
                return False
            if not any(self._asks_for_input(part) for part in [tab, *frames]):
                return True
            # A form on a confirmation page is an offer ("create a Candidate Home account to track your
            # progress"), not a step still to do -- when the page says in the past tense that the application
            # was received, and does not say there is something left to finish it.
            return any(RECEIVED_TEXT.search(t or "") and not STILL_TO_DO_TEXT.search(t or "") for t in texts)
        except Exception:
            return False

    @staticmethod
    def _asks_for_input(part) -> bool:
        """A password box, or any form field to fill in, is visible in this page or frame."""
        found = part.evaluate("""() => {
            const shown = e => !!(e.offsetParent || e.getClientRects().length) && getComputedStyle(e).visibility !== 'hidden';
            const skip = ['hidden', 'button', 'submit', 'image', 'search', 'reset'];
            const allElements = [...document.querySelectorAll('input, textarea, select, [role="textbox"], [role="combobox"], [role="listbox"], [role="radio"], [role="checkbox"], [contenteditable="true"]')]
                .filter(e => shown(e) && !skip.includes((e.type || '').toLowerCase()));
            const password = allElements.some(e => (e.type || '').toLowerCase() === 'password');
            const inputs = allElements.filter(e => (e.type || '').toLowerCase() !== 'button' && (e.type || '').toLowerCase() !== 'submit');
            return {password: password, count: inputs.length};
        }""")
        return bool(found["password"]) or found["count"] >= 1

    def _resume_on_page(self, page) -> bool:
        if not self.resume_file:
            return False
        try:
            return self.resume_file.name.lower() in self.snapshot(page).lower() or \
                self.resume_file.stem.lower() in self.tab(page).inner_text("body", timeout=5_000).lower()
        except Exception:
            return False

    def _resume_went_on(self, path) -> None:
        """The resume is on the form: remembered for this run, for later runs (an event), and by the browser."""
        self.resume_uploaded = True
        self._record_resume_attached()
        if hasattr(self.assistant, "attached_resume"):
            self.assistant.attached_resume = str(path)

    def _record_resume_attached(self) -> None:
        if self.tracker is not None and self.key and self.resume_file and hasattr(self.tracker, "record_event"):
            try:
                self.tracker.record_event(self.key, "resume_attached", self.resume_file.name)
            except Exception as exc:
                logger.debug("Could not record the upload: %s", exc)

    def _resume_attached_before(self) -> bool:
        """This tailored resume was attached to this application in an earlier
        run. Schwab's resume went on at step 1; the run that reached the last
        step started at step 4 and never saw it."""
        if self.tracker is None or not self.key or not self.resume_file or not hasattr(self.tracker, "events"):
            return False
        try:
            return any(e.get("kind") == "resume_attached" and e.get("message") == self.resume_file.name
                       for e in self.tracker.events(self.key, limit=300))
        except Exception:
            return False

    def read_when_loaded(self, page, wait_seconds: float = 15.0) -> str:
        """The page's snapshot once the frames on it have content.

        After Schwab's step-4 Submit the next form was still loading inside an
        inner frame; the agent read the empty frame, found no way forward and
        stopped. A frame with nothing in it is waited for, up to a limit.
        """
        deadline = time.time() + wait_seconds
        snapshot = self.snapshot(page)
        while (frames_loading(snapshot) or still_loading(snapshot) or workday_form_loading(snapshot)) \
            and time.time() < deadline:
            self.tab(page).wait_for_timeout(1_000)
            snapshot = self.snapshot(page)
        try:
            self._entries = repeated_entries.entry_map(snapshot)
        except Exception as exc:
            logger.debug("Could not read the page's repeated entries: %s", exc)
            self._entries = {}
        return snapshot

    def _save(self, snapshot: str) -> None:
        """Every page read is kept: it is what a failure is replayed from."""
        if self.resume_file and self.resume_file.name.lower() in (snapshot or "").lower():
            self.resume_seen = True   # the tailored resume shows as attached on a page of this run
        if not self.job_dir:
            return
        try:
            # One folder per run: every run numbered its pages from 1 in the same folder, so each run's pages
            # were written over the last one's, and the pages a failure happened on were gone by the next try.
            folder = Path(self.job_dir) / "pages" / self._run_stamp
            if not folder.is_dir():
                folder.mkdir(parents=True, exist_ok=True)
                keep_latest_runs(folder.parent, RUNS_KEPT)
            (folder / f"page_{self.pages_read:02d}.txt").write_text(snapshot, encoding="utf-8")
        except Exception:
            pass
