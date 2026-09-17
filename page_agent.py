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
  * never tick a legal declaration or signature
  * never type a password (the sign-in code handles employer accounts)
  * never change an answer the agent did not write
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
    context: str = ""                     # nearby text, when the control has no name of its own
    options: list[str] = field(default_factory=list)
    selected_option: str = ""

    @property
    def question(self) -> str:
        return self.group or self.name or self.context

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
        control = None
        if role in OPTION_ROLES and owner is not None and owner.role in ("combobox", "listbox"):
            owner.options.append(name or value)
            if "[selected]" in attrs:
                owner.selected_option = name or value
        # Choices drawn by script have references of their own, and are clicked.
        if ref_m and (role in ANSWER_ROLES or role in PRESS_ROLES or role in OPTION_ROLES):
            if role == "radio" and not (controls and controls[-1].role == "radio"):
                radio_context = last_text
            control = Control(
                ref=ref_m.group(1), role=role, name=name, value=value if value and not value.endswith(":") else "",
                checked="[checked]" in attrs or "[checked=true]" in attrs, selected="[selected]" in attrs,
                disabled="[disabled]" in attrs, group=(parent_group or radio_context) if role == "radio" else "",
                context="" if name else last_text)
            controls.append(control)
        if role in ("group", "radiogroup") and name:
            stack.append((indent, name, None))
        elif control is not None:
            stack.append((indent, role, control))
        else:
            stack.append((indent, "", None))
    return controls


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
        self._signed_in_at: set[str] = set()
        self.notes: list[str] = []                 # worth telling the owner, not worth stopping for

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

            # A password box is the sign-in code's: it types only the employer
            # account password, and never a Google one.
            if self._password_box(tab):
                email = getattr(self.config, "ats_email", "") or getattr(self.profile, "email", "")
                host = urlparse(tab.url).netloc
                if host in self._signed_in_at:
                    return Outcome("owner_needed", page, ["the sign-in did not go through -- it needs you"])
                self._signed_in_at.add(host)
                if self.assistant.handle_auth_gate(tab, email):
                    self.settle(page)
                    continue
                if self._password_box(tab):
                    return Outcome("owner_needed", page, ["a sign-in needs you (the password was not accepted "
                                                          "or there is no employer account password set)"])

            snapshot = self.snapshot(page)
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
            try:
                plan = PagePlan.from_json(self.claude.plan_page(compact_snapshot(snapshot), self.facts(controls),
                                                               feedback))
            except Exception as exc:
                return Outcome("owner_needed", page, [f"could not read this page ({str(exc).splitlines()[0][:100]})"])
            logger.info("READ: %s%s -- %d to answer, %d for you, next: %s %r", plan.page_kind.replace("_", " "),
                        f" ({plan.step})" if plan.step else "", len(plan.answers), len(plan.for_owner),
                        plan.next_kind.replace("_", " "), plan.next_label[:40])

            if plan.page_kind == "captcha":
                return Outcome("captcha", page, ["a CAPTCHA is showing -- only you can complete it"])
            if plan.page_kind == "confirmation" and self._site_confirms(tab):
                if self.final_pressed:
                    return Outcome("submitted", page, ["the site confirmed the application"])
                return Outcome("owner_needed", page, ["the site says this application was already sent"])

            given = self.apply_answers(page, plan, controls)

            # Read back what is on the page now, whoever filled it, and check
            # every answer the agent gave is really there.
            self.settle(page, 800)
            after = parse_snapshot(self.snapshot(page))
            missing = self.not_stuck(given, after)
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
        """Gives the plan's answers the rules allow; returns the ones given."""
        given: list[tuple[Answer, Control]] = []
        by_ref = {c.ref: c for c in controls}
        for answer in plan.answers:
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
            if done:
                given.append((answer, control))
                self.written[control.question or answer.question] = answer.value
                self.assistant.values.record(self.tab(page), f"aria:{control.question}", answer.value,
                                             answer.source or "agent")
                logger.info("ANSWERED: %r -> %r (from %s)", (control.question or answer.question)[:60],
                            answer.value[:50], answer.source or "?")
                self._remember(control, answer)
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

    def refusal(self, page, answer: Answer, control: Control, controls: list[Control] = ()) -> str:
        """Why this answer must not be given, or "" when it may."""
        question = control.question or answer.question
        action = answer.action
        if action not in ("fill", "choose", "check", "uncheck", "upload_resume", "upload_cover_letter"):
            return f"not something the agent does ({action})"
        if control.disabled:
            return "the field is disabled"
        if safety.is_attestation(question) or safety.is_attestation(control.name):
            if not (action == "check" and safety.is_privacy_consent(question)
                    and getattr(self.profile, "accept_application_privacy_prompts", False)):
                return "a declaration or signature -- only you can give it"
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
        if action in ("fill", "choose") and current and not self._ours(question, current):
            return f"already answered {current[:40]!r} -- not changing an answer the agent didn't give"
        if control.role == "radio" and action == "check" and control.group:
            chosen = next((c.name for c in controls if c.role == "radio" and c.group == control.group and c.checked), "")
            if chosen and not self._ours(question, chosen):
                return f"already answered {chosen[:40]!r} -- not changing an answer the agent didn't give"
        if control.role in ("checkbox", "switch") and action == "uncheck" and control.checked                 and not self._ours(question, answer.value or "checked"):
            return "ticked by someone else -- not unticking it"
        return ""

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
                if hasattr(self.assistant, "attached_resume"):
                    self.assistant.attached_resume = str(path)
            self.settle(page, 1_500)
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
        before = {c.ref for c in parse_snapshot(self.snapshot(page))}
        loc.click(timeout=5_000)
        tab.wait_for_timeout(700)
        options = [c for c in parse_snapshot(self.snapshot(page)) if c.role in OPTION_ROLES and c.ref not in before]
        if not options or len(options) > 40:
            # A type-ahead list shows its choices once something is typed.
            try:
                typing = self.locate(page, control.ref)
                if control.role in ("combobox", "searchbox", "textbox"):
                    typing.fill(value, timeout=4_000)
                else:
                    tab.keyboard.type(value, delay=30)
                tab.wait_for_timeout(1_000)
            except Exception:
                pass
            options = [c for c in parse_snapshot(self.snapshot(page)) if c.role in OPTION_ROLES]
        names = [c.name or c.value for c in options]
        index = best(names, [value]) if names else None
        if index is None:
            tab.keyboard.press("Escape")
            logger.info("No choice matching %r for %r among %s", value, control.question[:50], names[:8])
            return False
        self.locate(page, options[index].ref).click(timeout=5_000)
        tab.wait_for_timeout(500)
        return True

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
            return "stop", page, "no way forward was found on this page"
        label = " ".join((control.name or plan.next_label).split())
        if control.disabled:
            return "retry", page, f"{label!r} is not enabled yet"
        if NEVER_PRESS.search(label):
            return "stop", page, f"the way forward looked like {label!r}, which the agent never presses"
        if plan.next_kind == "sign_in" and re.search(r"google", label, re.IGNORECASE):
            email = getattr(self.profile, "email", "")
            self.assistant.sign_in_with_google_if_offered(tab, email)
            self.settle(page)
            return "moved", page, ""
        if re.search(r"linked ?in|indeed|facebook|apple|microsoft", label, re.IGNORECASE):
            return "stop", page, f"{label!r} is a sign-in the agent never uses"
        if plan.next_kind == "consent" or re.search(r"(i )?(agree|accept|acknowledge|consent)", label, re.IGNORECASE):
            if safety.is_attestation(label) or re.search(r"certif|attest|sign", label, re.IGNORECASE):
                return "stop", page, f"{label!r} is a declaration -- only you can give it"
            if not getattr(self.profile, "accept_application_privacy_prompts", False):
                return "stop", page, f"{label!r} accepts a notice -- you haven't allowed the agent to accept those"

        final = plan.next_kind == "final_submit" or (
            safety.is_submit_label(label) and plan.page_kind not in ("job_description",))
        if plan.page_kind == "job_description" and plan.next_kind == "open_application":
            final = False
        if final:
            gate = self.submit_gate(page, controls)
            if gate:
                return "stop", page, gate
            logger.info("SUBMITTING: pressing %r -- every check passed", label)
        else:
            logger.info("NEXT: pressing %r", label)

        before = re.sub(r"\[ref=[\w-]+\]|\[active\]", "", self.snapshot(page))
        tabs_before = len(tab.context.pages)
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

    def submit_gate(self, page, controls: list[Control]) -> str:
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
        if self.resume_file and not self.resume_uploaded and not self._resume_on_page(page):
            return "the tailored resume is not attached"
        return ""

    # -- small helpers --------------------------------------------------------------------
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

    def _save(self, snapshot: str) -> None:
        """Every page read is kept: it is what a failure is replayed from."""
        if not self.job_dir:
            return
        try:
            folder = self.job_dir / "pages"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"page_{self.pages_read:02d}.txt").write_text(snapshot, encoding="utf-8")
        except Exception:
            pass
