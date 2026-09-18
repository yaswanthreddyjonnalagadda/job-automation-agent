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

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import safety

logger = logging.getLogger("page_agent")

MAX_PAGES = 30          # steps in one run before handing over
MAX_TRIES_PER_PAGE = 3  # reads of the same page that failed to move it on

# Roles that take an answer or move the application on.
ANSWER_ROLES = {"textbox", "searchbox", "combobox", "listbox", "radio", "checkbox", "switch", "spinbutton", "slider"}
PRESS_ROLES = {"button", "link", "menuitem", "tab"}
OPTION_ROLES = {"option", "menuitemradio", "menuitem", "treeitem", "listitem", "gridcell"}

# Never pressed, whatever the page or Claude says.
NEVER_PRESS = re.compile(
    r"finish later|save (and|&) (exit|finish later|close)|save for later|withdraw|log ?out|sign ?out|\bcancel\b|"
    r"\bback\b|previous|\bdelete\b|\bremove\b|linked ?in|\bindeed\b|facebook|twitter|\bprint\b",
    re.IGNORECASE)

CONFIRMATION_TEXT = re.compile(
    r"thank you for (applying|your application|your interest)|application (has been |was )?(received|submitted)|"
    r"(successfully|now) submitted|we('ve| have) received your application|your application is complete|"
    r"you('ve| have) (successfully )?applied",
    re.IGNORECASE)

# What a site says when the account behind a sign-in cannot be used.
ACCOUNT_ERROR = re.compile(
    r"account is inactive|account (has been |is )?(inactive|disabled|locked|deactivated|suspended)|"
    r"no account (was )?found|user not found|we (could not|couldn't) find (an )?account|"
    r"sign[- ]?in (failed|unsuccessful)|unable to sign you in",
    re.IGNORECASE)

PLACEHOLDER = re.compile(
    r"^[\s\-–—]*(no selection|select( one| an option)?|please select|choose( one)?|make a selection|"
    r"none selected)?[\s\-–—.]*$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Reading the page
# ---------------------------------------------------------------------------
@dataclass
class Control:
    ref: str
    role: str
    name: str = ""
    value: str = ""
    checked: bool = False
    selected: bool = False
    disabled: bool = False
    group: str = ""                       # the question a radio button belongs to
    container: str = ""                   # the section it sits in ("Cover Letter")
    context: str = ""                     # nearby text, when the control has no name of its own
    options: list[str] = field(default_factory=list)
    selected_option: str = ""

    GENERIC_NAMES = re.compile(r"^(attach|choose file|upload( file)?|browse|add file|select file)$", re.IGNORECASE)

    @property
    def question(self) -> str:
        if self.group:
            return self.group
        if self.name and not self.GENERIC_NAMES.match(self.name.strip()):
            return self.name
        return self.container or self.name or self.context

    @property
    def answer(self) -> str:
        """What the control shows as answered, "" when nothing is."""
        if self.role in ("radio", "checkbox", "switch"):
            return "checked" if self.checked else ""
        shown = self.selected_option or self.value
        return "" if PLACEHOLDER.match(shown or "") else shown.strip()


_LINE = re.compile(r'^(?P<indent>\s*)- (?P<role>[\w/-]+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?(?P<attrs>(?: \[[^\]]*\])*)'
                   r'(?::\s?(?P<value>.*))?$')


def _unquote(text: str) -> str:
    text = (text or "").strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1]
    return text.replace('\\"', '"').replace("\\\\", "\\")


def parse_snapshot(snapshot: str) -> list[Control]:
    """The answerable and pressable controls in an accessibility snapshot, in
    page order, each with its reference, current answer and choices."""
    controls: list[Control] = []
    stack: list[tuple[int, str, Optional[Control]]] = []   # (indent, role/name, control)
    last_text = ""
    last_text_indent = -1
    radio_context = ""   # the question above a run of radio buttons with no group of their own
    for raw in (snapshot or "").splitlines():
        # A line whose text holds a colon comes wrapped in quotes:
        #   - 'heading "Apply: Network Engineer" [level=1] [ref=e2]'
        #   - 'combobox "Country: where you live" [ref=e20]': United States
        quoted = re.match(r"^(\s*)- '((?:[^']|'')*)'(:.*)?$", raw)
        if quoted:
            raw = f"{quoted.group(1)}- {quoted.group(2).replace(chr(39) * 2, chr(39))}{quoted.group(3) or ''}"
        m = _LINE.match(raw)
        if not m:
            continue
        indent, role = len(m.group("indent")), m.group("role")
        name = _unquote(m.group("name") or "")
        attrs = m.group("attrs") or ""
        value = _unquote(m.group("value") or "")
        ref_m = re.search(r"\[ref=([\w-]+)\]", attrs)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent_group = next((label for _i, label, ctl in reversed(stack)
                             if ctl is None and label), "")
        owner = next((ctl for _i, _l, ctl in reversed(stack) if ctl is not None), None)

        if role in ("text", "paragraph", "heading", "strong", "emphasis", "generic") and (value or name):
            last_text = (value or name).strip()[:200]
            last_text_indent = indent
            # Schwab's veteran question draws its radio buttons with no label of
            # their own: each is followed by its text ("I AM NOT A PROTECTED
            # VETERAN"). That text is the button's name.
            if controls and not controls[-1].name and controls[-1].role in ("radio", "checkbox", "switch") \
                    and not controls[-1].value and role == "text":
                controls[-1].name = (value or name).strip()[:200]
        control = None
        if role in OPTION_ROLES and owner is not None and owner.role in ("combobox", "listbox"):
            owner.options.append(name or value)
            if "[selected]" in attrs:
                owner.selected_option = name or value
        # Choices drawn by script have references of their own, and are clicked.
        if ref_m and (role in ANSWER_ROLES or role in PRESS_ROLES or role in OPTION_ROLES):
            if role == "radio" and not (controls and controls[-1].role == "radio"):
                radio_context = last_text
            shown = value if value and not value.endswith(":") else ""
            # Some widgets put the chosen value in a box beside the control
            # rather than in it ("Yes." next to "Would you relocate to London?",
            # "+1" next to "Country"). The text right beside it, at the same
            # level, is that choice -- unless it is the question's own label.
            if not shown and role in ("combobox", "listbox") and last_text and last_text_indent == indent \
                    and not _same_question(last_text, name):
                shown = last_text
            control = Control(
                ref=ref_m.group(1), role=role, name=name, value=shown,
                checked="[checked]" in attrs or "[checked=true]" in attrs, selected="[selected]" in attrs,
                disabled="[disabled]" in attrs, group=(parent_group or radio_context) if role == "radio" else "",
                container=parent_group, context="" if name else last_text)
            controls.append(control)
        if role in ("group", "radiogroup") and name:
            stack.append((indent, name, None))
        elif control is not None:
            stack.append((indent, role, control))
        else:
            stack.append((indent, "", None))
    return controls


def _section_holds_a_file(snapshot: str, section: str) -> bool:
    """True when the section already shows an attached file."""
    if not section:
        return False
    lines = (snapshot or "").splitlines()
    for i, line in enumerate(lines):
        if not re.search(rf'- (group|region) "{re.escape(section)}[^"]*"', line):
            continue
        indent = len(line) - len(line.lstrip())
        for later in lines[i + 1:]:
            if later.strip() and len(later) - len(later.lstrip()) <= indent:
                break
            if re.search(r"\.(pdf|docx?|txt|rtf)\b|remove file", later, re.IGNORECASE):
                return True
    return False


def host_of(url: str) -> str:
    return urlparse(url or "").netloc


# The name boxes a form asks for, and the profile field each one takes.
_NAME_FIELDS = (
    (re.compile(r"^\W*(legal\s+|given\s+)?first\s*(name)?\b", re.IGNORECASE), "first_name"),
    (re.compile(r"^\W*(legal\s+)?(last|family|sur)\s*name\b", re.IGNORECASE), "last_name"),
    (re.compile(r"^\W*(legal\s+)?middle\s*(name|initial)?\b", re.IGNORECASE), "middle_name"),
    (re.compile(r"^\W*(full|legal)\s*(legal\s*)?name\b", re.IGNORECASE), "full_name"),
)


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


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _same_question(a: str, b: str) -> bool:
    """The same question, whatever asterisks, punctuation or truncation differ."""
    a, b = _plain(a), _plain(b)
    if not a or not b:
        return False
    return a == b or (min(len(a), len(b)) >= 25 and (a.startswith(b[:60]) or b.startswith(a[:60])))


def _same_answer(a: str, b: str) -> bool:
    a, b = _plain(a), _plain(b)
    return bool(a) and bool(b) and (a == b or a.startswith(b + " ") or b.startswith(a + " "))


def frames_loading(snapshot: str) -> bool:
    """True when a frame on the page has nothing in it yet."""
    lines = [l for l in (snapshot or "").splitlines() if l.strip()]
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)- iframe\b[^\n]*:\s*$", line)
        if not m:
            continue
        indent = len(m.group(1))
        children = []
        for later in lines[i + 1:]:
            if len(later) - len(later.lstrip()) <= indent:
                break
            children.append(later.strip())
        if not [c for c in children if re.sub(r"^- (text|generic)(\s*\[[^\]]*\])*:?\s*$", "", c)]:
            return True
    return False


def choices_in(snapshot: str) -> list[tuple[str, str]]:
    """(reference, text) for every choice a list is showing.

    Some lists (Greenhouse's) give their options no name of their own and put
    the words in a child element, so the agent read them as empty and could
    choose nothing -- School, Discipline and Veteran Status were all left blank.
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


def closest_choice(choices: list[str], wanted: str) -> Optional[int]:
    """The choice that says what the profile says, where the words differ.

    The profile says "I am not a veteran"; the form offers "I am not a
    protected veteran". A choice that reverses the meaning ("I identify as
    ...") is never taken: a yes/no in the answer must appear in the choice too.
    """
    want = set(_plain(wanted).split()) - {"i", "a", "an", "the", "of", "or", "am", "is", "are", "to", "my"}
    if not want:
        return None
    negative = bool(re.search(r"\b(not|no|never|none)\b", _plain(wanted)))
    best, best_score = None, 0.0
    for i, choice in enumerate(choices):
        words = set(_plain(choice).split())
        if not words or negative != bool(re.search(r"\b(not|no|never|none)\b", _plain(choice))):
            continue
        score = len(want & words) / len(want)
        if score > best_score:
            best, best_score = i, score
    return best if best_score >= 0.6 else None


def compact_snapshot(snapshot: str, limit: int = 60_000) -> str:
    """The snapshot without its empty containers and link addresses, which
    carry no meaning for answering a form and cost most of the space."""
    kept = [line[:400] for line in (snapshot or "").splitlines()
            if not re.match(r"^\s*- generic( \[active\])? \[ref=[\w-]+\]:?$", line)
            and not re.match(r"^\s*- /url:", line)]
    text = "\n".join(kept)
    return text if len(text) <= limit else text[:limit] + "\n... (page continues)"


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
        self.resume_file = Path(resume_file) if resume_file else None
        self.cover_letter = cover_letter
        self.resume_uploaded = False
        self.final_pressed = False
        self.pages_read = 0
        self.written: dict[str, str] = {}          # question -> value the agent put there
        self._letter: Optional[tuple[Path, Path]] = None
        self._letter_attached = False
        self._code_tries = 0
        self._last_code = ""
        self._signed_in_at: set[str] = set()
        self._google_tried: set[str] = set()
        self._retried_after_error = False
        self._google_failed: set[str] = set()   # sites whose Google account they will not accept
        self._emailed_in: set[str] = set()      # sites where the email has been given on its own page
        self._created_at: set[str] = set()      # sites where an account has been created
        self.notes: list[str] = []                 # worth telling the owner, not worth stopping for
        self.corrected: set[str] = set()           # questions the agent put right from the profile
        self.owner_answers: dict[str, str] = {}    # answers the owner set while the agent waited
        self._paused_state: dict[str, str] = {}

    def forget_sign_in_attempts(self) -> None:
        """Google stays refused where the site itself rejected the account."""
        """A resumed run tries signing in again: the owner has had a hand in it,
        and the page may now offer something the last attempt never saw."""
        # What the site rejected is remembered; only the password attempts are
        # forgiven, so a resumed run can try them again.
        self._signed_in_at = set()
        self._emailed_in = set()
        self._retried_after_error = False

    def _ensure_state(self) -> None:
        """Code reloaded into a run that is already going keeps its old object:
        anything added since starts empty rather than failing."""
        for name, default in (("notes", list), ("corrected", set), ("owner_answers", dict), ("failed", list),
                              ("_google_tried", set), ("_retried_after_error", bool), ("_google_failed", set),
                              ("_emailed_in", set), ("_created_at", set), ("_letter_attached", bool), ("_code_tries", int), ("_last_code", str),
                              ("_paused_state", dict), ("_signed_in_at", set), ("written", dict)):
            if not hasattr(self, name):
                setattr(self, name, default())

    # -- the page --------------------------------------------------------------
    @staticmethod
    def tab(page):
        return getattr(page, "top", None) or page

    def snapshot(self, page) -> str:
        return self.tab(page).locator("body").aria_snapshot(mode="ai", timeout=20_000)

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
    GOOGLE_SIGN_IN = re.compile(r"(sign|log) ?in with google|continue with google|google sign[- ]?in", re.IGNORECASE)

    def sign_in_step(self, page, controls: list[Control]) -> bool:
        """Signs in where the page asks for it. True when something was done.

        Google first, always, when the site offers it -- the owner's standing
        instruction. NVIDIA's (Workday) page offered "Sign in with Google",
        "Sign in with LinkedIn" and "Sign in with email"; the agent took email
        and got nowhere. LinkedIn, Indeed, Facebook, Apple and Microsoft are
        never used. Otherwise the employer-account password, once per site.
        """
        tab = self.tab(page)
        email = getattr(self.config, "ats_email", "") or getattr(self.profile, "email", "")
        host = urlparse(tab.url).netloc
        google = next((c for c in controls if c.role in PRESS_ROLES and self.GOOGLE_SIGN_IN.search(c.name or "")), None)
        if google is not None and host not in self._google_tried and host not in self._google_failed and email:
            self._google_tried.add(host)
            logger.info("LOGIN: this site offers Google sign-in -- using it")
            try:
                # Through the snapshot's reference, so a sign-in box inside a
                # frame is reached as well.
                button = self.locate(page, google.ref).element_handle(timeout=5_000)
                if self.assistant._sign_in_with_google(tab, button, email, host, lambda: None):
                    logger.info("LOGIN: signed in with Google")
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
        # A sign-in that asks for the email on its own page first (Workday's
        # does, after "Sign in with email"): give it, and press on. The password
        # is never typed here -- that is the sign-in code's, below.
        if not self._password_box(tab) and email and host not in self._emailed_in:
            box = next((c for c in controls
                        if c.role in ("textbox", "searchbox") and not c.answer
                        and re.search(r"e-?mail|user ?name", f"{c.name} {c.question}", re.IGNORECASE)), None)
            if box is not None:
                self._emailed_in.add(host)
                logger.info("LOGIN: this sign-in asks for the email first -- giving it")
                try:
                    self.locate(page, box.ref).fill(email, timeout=8_000)
                    go = next((c for c in controls if c.role in PRESS_ROLES and re.match(
                        r"^(sign ?in|log ?in|continue|next|submit)$",
                        " ".join((c.name or "").split()), re.IGNORECASE)), None)
                    if go is not None:
                        self.locate(page, go.ref).click(timeout=8_000)
                    else:
                        self.locate(page, box.ref).press("Enter", timeout=5_000)
                    return True
                except Exception as exc:
                    logger.warning("LOGIN: could not give the email (%s)", str(exc).splitlines()[0][:100])
        # Two password boxes mean "create an account": its own step, which the
        # sign-in code fills (password, repeat, the site's terms), and not a
        # repeat of a sign-in already tried here.
        making_account = self._password_boxes(tab) >= 2
        if making_account and host in self._created_at:
            return False
        if self._password_box(tab) and (making_account or host not in self._signed_in_at):
            (self._created_at if making_account else self._signed_in_at).add(host)
            logger.info("LOGIN: %s on %s", "creating the account" if making_account else "signing in", host)
            try:
                if self.assistant.handle_auth_gate(tab, email):
                    return True
            except Exception as exc:
                logger.warning("LOGIN: %s", str(exc).splitlines()[0][:120])
        return False

    # A code emailed to the owner for their own account or email address.
    ACCOUNT_CODE = re.compile(
        r"(verification|security|confirmation|one[- ]?time|access)\s*code|passcode|\botp\b|"
        r"code (was )?(sent|emailed) to", re.IGNORECASE)
    # A code a site asks for to prove a human is applying: never the agent's.
    HUMAN_CHECK = re.compile(
        r"confirm (that )?(you'?re|you are) (a )?human|prove (that )?you('re| are) (a )?human|"
        r"not a robot|human verification|verify (that )?you are (a )?human", re.IGNORECASE)

    def complete_account_code(self, page, controls: list[Control], snapshot: str) -> bool:
        """Enters a one-time code emailed to the owner for their own account.

        The owner approved this on 2026-09-15 for account setup and sign-in
        (browser_automation.complete_emailed_passcode), and R+L Carriers' form
        asks for exactly that. A code a site asks for to prove a human is
        applying is refused -- Harbinger's says so beside its reCAPTCHA, and
        that one is the owner's to enter.
        """
        # R+L Carriers gives the code a box per digit ("Enter verification code
        # digit 1 of six."), so one box or six, they are all the same step.
        boxes = [c for c in controls
                 if c.role in ("textbox", "searchbox", "spinbutton") and not c.answer and not c.disabled
                 and self.ACCOUNT_CODE.search(f"{c.question} {c.container}")]
        if not boxes or self._code_tries >= 3:
            return False
        if not getattr(self.profile, "check_gmail_for_confirmation", False):
            logger.info("A code was emailed to you; the agent may not read your mail")
            return False
        if self.HUMAN_CHECK.search(snapshot) or safety.captcha_visible(page):
            logger.info("This code is asked for to prove a human is applying -- that one is yours to enter")
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
            if not code:
                logger.info("CODE: it has not arrived yet -- the agent will look again")
                return False
            self._last_code = code
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
                first.fill(code, timeout=5_000)
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

    # -- facts Claude may answer from ---------------------------------------------
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
            "job": {"title": getattr(self.job, "title", ""), "company": getattr(self.job, "company", "")},
            "profile": profile,
            "phone_with_country_code": f"{code} {phone}".strip() if code and not phone.startswith("+") else phone,
            "resume_text": (getattr(self.resume, "raw_text", "") or "")[:12_000],
            "owner_earlier_answers": earlier[:40],
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
            try:
                self.assistant.dismiss_cookie_banner(tab)
            except Exception:
                pass
            if safety.captcha_visible(page):
                return Outcome("captcha", page, ["a CAPTCHA is showing -- only you can complete it"])
            if getattr(self.profile, "requires_visa_sponsorship", False):
                try:
                    said = safety.no_sponsorship_statement(tab.inner_text("body", timeout=5_000))
                except Exception:
                    said = ""
                if said:
                    return Outcome("no_sponsorship", page, [said])

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
            if self.sign_in_step(page, controls):
                self.settle(page)
                continue
            if self.complete_account_code(page, controls, snapshot):
                self.settle(page)
                continue
            try:
                plan = PagePlan.from_json(self.claude.plan_page(compact_snapshot(snapshot), self.facts(controls),
                                                               feedback))
            except Exception as exc:
                message = str(exc)
                if "out of credit" in message or "credit balance" in message.lower():
                    return Outcome("owner_needed", page, [message.split(": ", 1)[-1][:200]])
                return Outcome("owner_needed", page, [f"could not read this page ({message.splitlines()[0][:120]})"])
            logger.info("READ: %s%s -- %d to answer, %d for you, next: %s %r", plan.page_kind.replace("_", " "),
                        f" ({plan.step})" if plan.step else "", len(plan.answers), len(plan.for_owner),
                        plan.next_kind.replace("_", " "), plan.next_label[:40])

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
            if plan.page_kind == "confirmation" and self._site_confirms(tab):
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
            missing = self.not_stuck(given, after) + list(getattr(self, "failed", []))
            if missing:
                stick_tries += 1
                if stick_tries < MAX_TRIES_PER_PAGE:
                    feedback = "these answers did not stay on the page, try another way: " + "; ".join(missing)
                    logger.info("NOT STUCK: %s -- reading the page again", "; ".join(missing)[:200])
                    continue
                return Outcome("owner_needed", page, [f"could not set: {m}" for m in missing])
            blockers = self.blockers(page, plan, after)
            if blockers:
                return Outcome("owner_needed", page, blockers)

            moved, page, feedback = self.press_next(page, plan, after)
            if moved == "moved":
                stick_tries = 0
            if moved == "submitted":
                return Outcome("submitted", page, ["the site confirmed the application"])
            if moved == "stop":
                return Outcome("owner_needed", page, [feedback])
        return Outcome("gave_up", page, ["too many pages in one run"])

    # -- answering ------------------------------------------------------------------
    def apply_answers(self, page, plan: PagePlan, controls: list[Control]) -> list[tuple[Answer, Control]]:
        """Gives the plan's answers the rules allow; returns the ones given.
        The ones that could not be given are kept in self.failed."""
        given: list[tuple[Answer, Control]] = []
        self.failed = []
        by_ref = {c.ref: c for c in controls}
        signing = [a for a in plan.answers if self._is_signature(a, by_ref.get(a.ref))]
        for answer in [a for a in plan.answers if a not in signing]:
            control = by_ref.get(answer.ref)
            if control is None:
                logger.info("SKIPPED: %r is not on the page", answer.question[:60])
                continue
            refusal = self.refusal(page, answer, control, controls)
            if refusal:
                logger.info("LEFT FOR YOU: %r -- %s", (control.question or answer.question)[:70], refusal)
                plan.for_owner.append({"question": control.question or answer.question, "reason": refusal,
                                       "required": "*" in (control.question or "")})
                continue
            try:
                done = self.do(page, answer, control)
            except Exception as exc:
                done = False
                logger.info("Could not answer %r: %s", answer.question[:60], str(exc).splitlines()[0][:100])
            if not done:
                question = (control.question or answer.question)[:70]
                if "*" in (control.question or "") or "*" in (control.name or ""):
                    self.failed.append(f"{question} = {answer.value[:40]!r}")
                else:
                    note = f"{question}: could not choose {answer.value[:40]!r} -- this form doesn't offer it"
                    if note not in self.notes:
                        self.notes.append(note)
                    logger.info("LEFT BLANK: %s", note)
            if done:
                given.append((answer, control))
                self.written[control.question or answer.question] = answer.value
                self.assistant.values.record(self.tab(page), f"aria:{control.question}", answer.value,
                                             answer.source or "agent")
                logger.info("ANSWERED: %r -> %r (from %s)", (control.question or answer.question)[:60],
                            answer.value[:50], answer.source or "?")
                self._remember(control, answer)
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

    def attach_documents(self, page, snapshot: str, controls: list[Control]) -> list[tuple[Answer, Control]]:
        """Attaches the resume and the cover letter where the form asks for them.

        Not left to the plan: Harbinger's form (Greenhouse) has a "Cover Letter"
        section whose only control is called "Attach", and the cover letter was
        forgotten again. A section that already holds a file is left alone.
        """
        given: list[tuple[Answer, Control]] = []
        wanted = (("resume", re.compile(r"resume|\bcv\b", re.IGNORECASE), "upload_resume"),
                  ("cover letter", re.compile(r"cover letter", re.IGNORECASE), "upload_cover_letter"))
        for what, matches, action in wanted:
            if action == "upload_resume" and (self.resume_uploaded or not self.resume_file):
                continue
            if action == "upload_cover_letter" and (self._letter_attached or self.cover_letter is None):
                continue
            control = next((c for c in controls
                            if c.role in PRESS_ROLES | {"button"} and matches.search(f"{c.container} {c.name}")
                            and re.search(r"attach|upload|choose|add|browse|file", c.name or "", re.IGNORECASE)), None)
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
            if done:
                given.append((answer, control))
                if action == "upload_cover_letter":
                    self._letter_attached = True
                logger.info("ATTACHED: the %s (%s)", what, Path(path).name)
        return given

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
                ok = bool(shown) and (want in shown or shown in want
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
        try:
            if (self.locate(page, control.ref).get_attribute("type", timeout=2_000) or "").lower() == "password":
                return "a password -- the agent never types those here"
        except Exception:
            pass
        if action in ("fill", "choose") and not answer.value.strip():
            return "no answer to give"
        if safety.is_legal_status_question(question) or safety._SPONSORSHIP_Q.search(question) \
                or safety._AUTHORIZED_Q.search(question):
            field_name = answer.source.split(".", 1)[1] if answer.source.startswith("profile.") else ""
            if not field_name or not str(getattr(self.profile, field_name, "") or "").strip():
                return "a legal or immigration question your profile doesn't state"
        current = control.answer
        if self._owner_gave(question):
            return "you answered this yourself -- left as you set it"
        if correcting:
            return ""
        if action in ("fill", "choose") and current and not self._ours(question, current):
            return f"already answered {current[:40]!r} -- not changing an answer the agent didn't give"
        if control.role == "radio" and action == "check" and control.group:
            chosen = next((c.name for c in controls if c.role == "radio" and c.group == control.group and c.checked), "")
            if chosen and not self._ours(question, chosen):
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
        # The owner's name as they write it. Forms were filled "Yaswanth" /
        # "Reddy Jonnalagadda", split from the full name.
        for control in controls:
            if control.role not in ("textbox", "searchbox") or control.disabled:
                continue
            field = _name_field(control.question)
            if not field:
                continue
            wanted = str(getattr(self.profile, field, "") or "").strip()
            shown = control.answer.strip()
            if wanted and shown.lower() != wanted.lower():
                # Exactly: "Yaswanth" is not "Yaswanth Reddy", though one
                # begins the other.
                fixes.append((control.question, wanted, f"profile.{field}", True))
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

        given: list[tuple[Answer, Control]] = []
        for question, value, source, exact in fixes:
            control = self._control_for(question, value, controls)
            if control is None:
                continue
            was = self._shown_answer(control, controls)
            if was and (_plain(was) == _plain(value) if exact else _same_answer(was, value)):
                continue
            action = ("check" if control.role in ("radio", "checkbox", "switch")
                      else "choose" if control.role in ("combobox", "listbox") else "fill")
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
        return given

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
        try:
            self._paused_state = {f["label"]: f["value"] for f in answered_fields(parse_snapshot(self.snapshot(page)))}
        except Exception:
            self._paused_state = {}

    def note_owner_changes(self, page) -> None:
        try:
            now = {f["label"]: f["value"] for f in answered_fields(parse_snapshot(self.snapshot(page)))}
        except Exception:
            return
        for question, value in now.items():
            if question in self._paused_state and value != self._paused_state[question]:
                self.owner_answers[question] = value
                logger.info("YOURS: %r is now %r -- the agent leaves it as you set it", question[:60], value[:40])

    def _owner_gave(self, question: str) -> bool:
        return any(_same_question(question, q) for q in self.owner_answers)

    def _ours(self, question: str, current: str) -> bool:
        wrote = self.written.get(question)
        return wrote is not None and (wrote.strip().lower() == current.strip().lower()
                                      or wrote.strip().lower() in current.strip().lower())

    def do(self, page, answer: Answer, control: Control) -> bool:
        tab = self.tab(page)
        loc = self.locate(page, control.ref)
        if answer.action == "fill":
            loc.fill(answer.value, timeout=8_000)
            try:
                loc.press("Tab", timeout=2_000)
            except Exception:
                pass
            return True
        if answer.action in ("check", "uncheck"):
            want = answer.action == "check"
            try:
                loc.set_checked(want, timeout=5_000)
            except Exception:
                loc.click(timeout=5_000)
            return True
        if answer.action in ("upload_resume", "upload_cover_letter"):
            path = self.resume_file if answer.action == "upload_resume" else self._letter_file()
            if not path or not Path(path).is_file():
                return False
            try:
                loc.set_input_files(str(path), timeout=8_000)
            except Exception:
                with tab.expect_file_chooser(timeout=8_000) as chooser:
                    loc.click(timeout=5_000)
                chooser.value.set_files(str(path))
            if answer.action == "upload_resume":
                self.resume_uploaded = True
                self._record_resume_attached()
                if hasattr(self.assistant, "attached_resume"):
                    self.assistant.attached_resume = str(path)
            self.settle(page, 1_500)
            return True
        if answer.action == "choose" and control.role in ("radio", "checkbox", "switch"):
            group = [c for c in parse_snapshot(self.snapshot(page))
                     if c.role == control.role and c.group and c.group == control.group] or [control]
            index = self.assistant._best_option([c.name for c in group], [answer.value])
            if index is None:
                logger.info("No choice matching %r for %r among %s", answer.value, control.question[:50],
                            [c.name for c in group][:8])
                return False
            target = self.locate(page, group[index].ref)
            try:
                target.set_checked(True, timeout=5_000)
            except Exception:
                target.click(timeout=5_000)
            return True
        if answer.action == "choose":
            return self.choose(page, control, answer.value)
        return False

    def choose(self, page, control: Control, value: str) -> bool:
        tab = self.tab(page)
        loc = self.locate(page, control.ref)
        best = self.assistant._best_option
        if control.options:
            index = best(control.options, [value])
            if index is None:
                logger.info("No choice matching %r for %r among %s", value, control.question[:50], control.options[:8])
                return False
            try:
                loc.select_option(label=control.options[index], timeout=5_000)
                return True
            except Exception:
                pass   # a listbox drawn by script: pick the option instead
        before = {ref for ref, _text in choices_in(self.snapshot(page))}
        loc.click(timeout=5_000)
        # A long list takes a moment to appear: read at 0.7s, it was empty, and
        # School, Discipline and Veteran Status were all left blank.
        offered = [(ref, text) for ref, text in self._wait_for_choices(page) if ref not in before]
        index = self._pick(offered, value)
        if index is None:
            # A type-ahead list shows its choices once something is typed, and a
            # long list needs narrowing down.
            for typed in self._search_terms(value):
                try:
                    # Typed, not set: a list that searches as you type only
                    # answers to real keystrokes.
                    typing = self.locate(page, control.ref)
                    typing.click(timeout=4_000)
                    if control.role in ("combobox", "searchbox", "textbox"):
                        typing.fill("", timeout=3_000)
                    tab.keyboard.type(typed, delay=40)
                except Exception:
                    break
                offered = self._wait_for_choices(page)
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
            tab.keyboard.press("Escape")
            logger.info("No choice matching %r for %r among %s", value, control.question[:50],
                        [t for _r, t in offered][:8])
            return False
        self.locate(page, offered[index][0]).click(timeout=5_000)
        tab.wait_for_timeout(500)
        return True

    def _wait_for_choices(self, page, seconds: float = 4.0) -> list[tuple[str, str]]:
        """The choices a list is showing, once it has finished showing them."""
        tab = self.tab(page)
        deadline = time.time() + seconds
        offered: list[tuple[str, str]] = []
        while time.time() < deadline:
            tab.wait_for_timeout(500)
            offered = choices_in(self.snapshot(page))
            if offered:
                break
        return offered

    def _pick(self, offered: list[tuple[str, str]], value: str) -> Optional[int]:
        """Which choice to take: the one that matches, else the closest."""
        names = [text for _ref, text in offered]
        if not names:
            return None
        index = self.assistant._best_option(names, [value])
        if index is None:
            index = closest_choice(names, value)
            if index is not None:
                logger.info("Closest choice to %r is %r", value[:40], names[index][:60])
        return index

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

    def _letter_file(self) -> Optional[Path]:
        if self._letter is None and self.cover_letter is not None:
            self._letter = self.cover_letter()
        return self._letter[1] if self._letter else None

    def _remember(self, control: Control, answer: Answer) -> None:
        if self.tracker is None or not hasattr(self.tracker, "record_answer") or not self.key:
            return
        if answer.action not in ("fill", "choose", "check") or answer.source.startswith("profile."):
            return
        try:
            self.tracker.record_answer(self.key, urlparse(getattr(self.job, "url", "")).netloc,
                                       control.question, answer.value, options=control.options or None,
                                       answered_by="claude")
        except Exception as exc:
            logger.debug("Could not record the answer: %s", exc)

    # -- what stops the run -----------------------------------------------------------
    def blockers(self, page, plan: PagePlan, controls: list[Control]) -> list[str]:
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
            if safety.is_legal_status_question(question) or safety._SPONSORSHIP_Q.search(question)                     or safety._AUTHORIZED_Q.search(question):
                reason = f"doesn't match your profile: {question[:90]} -- the page says {said!r}"
                if not any(question[:60] in r for r in reasons):
                    reasons.append(reason)
            else:
                note = f"{question[:80]}: the page says {said!r}, your profile says {str(mismatch.get('facts_say') or '')[:60]!r}"
                if note not in self.notes:
                    self.notes.append(note)
        for item in plan.for_owner:
            question = str(item.get("question") or "")
            required = bool(item.get("required")) or "*" in question
            still_blank = not any(c.question == question and c.answer for c in controls)
            if required and still_blank:
                reasons.append(f"needs your answer: {question[:90]} ({item.get('reason', '')})")
        try:
            pending = self.assistant.pending_attestations(self.tab(page))
        except Exception:
            pending = []
        if pending and plan.next_kind == "final_submit":
            reasons += [f"your declaration or signature: {p[:90]}" for p in pending]
        return reasons

    # -- moving on ----------------------------------------------------------------------
    def press_next(self, page, plan: PagePlan, controls: list[Control]) -> tuple[str, object, str]:
        """("moved" | "submitted" | "retry" | "stop", page, what happened)."""
        tab = self.tab(page)
        by_ref = {c.ref: c for c in controls}
        control = by_ref.get(plan.next_ref)
        if plan.next_kind == "none" or control is None:
            # Nothing to press may only mean the page is still arriving: read it
            # again (the same page counts towards the tries) before giving up.
            self.settle(page, 3_000)
            return "retry", page, "no way forward was found on this page"
        label = " ".join((control.name or plan.next_label).split())
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
        if re.search(r"linked ?in|indeed|facebook|apple|microsoft", label, re.IGNORECASE):
            return "stop", page, f"{label!r} is a sign-in the agent never uses"
        if plan.next_kind == "consent" or re.search(r"(i )?(agree|accept|acknowledge|consent)", label, re.IGNORECASE):
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
        final = plan.next_kind == "final_submit" or (submit_word and not (steps_remain and plan.next_kind == "next_step"))
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

        before = re.sub(r"\[ref=[\w-]+\]|\[active\]", "", self.snapshot(page))
        tabs_before = len(tab.context.pages)
        if submit_word:
            self.final_pressed = True   # a confirmation after any Submit counts
        pressed_at = time.time()
        self.locate(page, control.ref).click(timeout=10_000)
        self.settle(page, 2_500)
        page = self.newest_tab(page, tabs_before)
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
        """Why the application must not be sent now, or "" when it may."""
        if not getattr(self.config, "auto_submit", False):
            return "automatic submission is off -- the application is ready for you to submit"
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
        if last_step and self.resume_file and not (self.resume_uploaded or getattr(self, "resume_seen", False)
                                                    or self._resume_on_page(page)
                                                    or self._resume_attached_before()):
            return "the tailored resume is not attached"
        return ""

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
        try:
            texts = [tab.inner_text("body", timeout=5_000)]
            texts += [f.locator("body").inner_text(timeout=3_000) for f in tab.frames[1:]
                      if (f.url or "").startswith("http") and not safety.is_captcha_frame(f.url)]
        except Exception:
            return False
        return any(CONFIRMATION_TEXT.search(t or "") for t in texts)

    def _resume_on_page(self, page) -> bool:
        if not self.resume_file:
            return False
        try:
            return self.resume_file.name.lower() in self.snapshot(page).lower() or \
                self.resume_file.stem.lower() in self.tab(page).inner_text("body", timeout=5_000).lower()
        except Exception:
            return False

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
        while frames_loading(snapshot) and time.time() < deadline:
            self.tab(page).wait_for_timeout(1_000)
            snapshot = self.snapshot(page)
        return snapshot

    def _save(self, snapshot: str) -> None:
        """Every page read is kept: it is what a failure is replayed from."""
        if self.resume_file and self.resume_file.name.lower() in (snapshot or "").lower():
            self.resume_seen = True   # the tailored resume shows as attached on a page of this run
        if not self.job_dir:
            return
        try:
            folder = self.job_dir / "pages"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"page_{self.pages_read:02d}.txt").write_text(snapshot, encoding="utf-8")
        except Exception:
            pass
