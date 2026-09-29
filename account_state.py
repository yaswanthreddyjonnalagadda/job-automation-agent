"""Which account step a page is at, on any employer site, and what the agent does next.

Signing in was decided in pieces, each reading the page its own way -- counting password boxes here, looking for a
Google button there, a Workday-only check elsewhere -- so a page one piece misread sent the run down the wrong road
(KBI Biopharma, 28 September: an account was created, the run then tried to sign in on a page it could not read, and
stopped with nothing to show why). This module is the one place that says what an account page is, from what the
page shows, and the one table that says what to do about it. The actions themselves (Google, creating the account,
signing in, entering an emailed code) are the agent's existing ones.

Every rule the owner set still holds: Google first when offered; LinkedIn, Indeed, Dice, Apple, Microsoft and
Facebook never; the employer-account password from .env, and only on employer sites; attempts limited by
login_guard; a verification link is the owner's to click.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# What an account page can be.
SIGNED_IN = "signed_in"             # past the account step: the application itself
LOCKED = "locked"                   # the site says the account is locked or disabled
WRONG_PASSWORD = "wrong_password"   # the site refused the email and password
ACCOUNT_EXISTS = "account_exists"   # creating one was refused: the email already has an account
CODE_ENTRY = "code_entry"           # a box for a code the site emailed
VERIFY_EMAIL = "verify_email"       # the site sent a link to click before the account can be used
CREATE_FORM = "create_form"         # a new-account form: email, password, and the password again
SIGN_IN_FORM = "sign_in_form"       # email (or not yet) and one password
EMAIL_FIRST = "email_first"         # the sign-in asks for the email on its own page first
CHOOSER = "chooser"                 # ways in to pick from: Google, email, create account, apply manually
LOADING = "loading"                 # the account step, still drawing itself
NONE = "none"                       # not an account page at all

_TEXT = re.compile(r'"((?:[^"\\]|\\.)*)"|:\s+(.+)$')
_PASSWORD_BOX = re.compile(r'- textbox "[^"]*pass ?word[^"]*"', re.IGNORECASE)
_EMAIL_BOX = re.compile(r'- textbox "[^"]*(e-?mail|user ?name)[^"]*"', re.IGNORECASE)
_STEP = re.compile(r"current step (\d+) of (\d+)", re.IGNORECASE)

_LOCKED = re.compile(
    r"account (is|has been) (locked|disabled|deactivated|inactive|suspended)|too many (failed |unsuccessful )?"
    r"(sign.?in |log.?in )?attempts|temporarily locked|account is inactive", re.IGNORECASE)
_WRONG_PASSWORD = re.compile(
    r"(wrong|incorrect|invalid) (email|e-mail|user ?name|password|credentials|sign.?in)|"
    r"(email|user ?name|password) (is|was|are) (incorrect|invalid|wrong)|does not match (our|any) (records|account)|"
    r"(could ?n.?t|unable to|failed to) (sign|log) (you )?in|sign.?in failed|login failed", re.IGNORECASE)
_EXISTS = re.compile(
    r"(account|user|email|e-mail)( address)? (already )?(exists|is already (registered|in use|taken))|"
    r"already (have|has) an account (with|for) (this|that)|already registered", re.IGNORECASE)
_CODE = re.compile(r"(verification|security|confirmation|one[- ]?time|access|sign.?in)\s*code|passcode|\botp\b",
                   re.IGNORECASE)
_VERIFY = re.compile(
    r"verify (your|the) (email|e-mail|account|address)|verification (email|e-mail|link)|check your (email|e-mail|inbox)|"
    r"we('ve| have)? sent (you )?(an? )?(email|e-mail|link|message)|activate your account|confirm your (email|account)",
    re.IGNORECASE)
_CREATE = re.compile(r"\bcreate (an |your )?account\b|\bregister\b|\bsign ?up\b", re.IGNORECASE)
_SIGN_IN = re.compile(r"\bsign ?in\b|\blog ?in\b", re.IGNORECASE)
_WAYS_IN = re.compile(r"sign in with (google|email|linkedin|apple|microsoft|facebook)|continue with (google|email)|"
                      r"apply manually|use my last application|autofill with resume", re.IGNORECASE)
# The form was sent and the site pointed at a box: the form is still to be finished, whatever else is known.
_FORM_ERROR = re.compile(
    r"passwords? (do not|don't|must) match|please check (the )?box|field is required|is a required field|"
    r"(email|password).{0,30}(invalid|required|must contain|too short)", re.IGNORECASE)
_GOOGLE = re.compile(r"(sign in|continue|log in) with google", re.IGNORECASE)
_ACCOUNT_STEP = re.compile(r"create account\s*/\s*sign in|sign in\s*/\s*create account", re.IGNORECASE)


@dataclass(frozen=True)
class AccountState:
    kind: str
    why: str = ""                    # the words on the page that decided it
    google_offered: bool = False
    password_boxes: int = 0
    form_error: str = ""             # what the site says is wrong with the form it is showing

    def __str__(self) -> str:
        return f"{self.kind}" + (f" ({self.why[:80]})" if self.why else "")


def _texts(snapshot: str) -> list[str]:
    """Every piece of text the page shows: names, values and plain text lines."""
    out = []
    for line in (snapshot or "").splitlines():
        if "/url:" in line:
            continue
        for quoted, trailing in _TEXT.findall(line.strip()):
            text = (quoted or trailing or "").strip()
            if text and not text.startswith("[ref="):
                out.append(text)
    return out


def _first(pattern: re.Pattern, texts: list[str]) -> str:
    return next((t for t in texts if pattern.search(t)), "")


def read_state(snapshot: str, password_boxes: Optional[int] = None) -> AccountState:
    """What account step the page is at, from what it shows. Most telling first: what the site SAYS (locked,
    wrong password, exists, verify) outranks what the form looks like, because those messages sit on the form.

    `password_boxes` is the count the page itself gives, when known: a password box with no label is not
    named as one in the snapshot."""
    texts = _texts(snapshot)
    passwords = max(len(_PASSWORD_BOX.findall(snapshot or "")), password_boxes or 0)
    form_error = _first(_FORM_ERROR, texts)
    google = bool(_first(_GOOGLE, texts))
    step = _STEP.search(snapshot or "")
    on_account_step = bool(_first(_ACCOUNT_STEP, texts))

    def state(kind, why=""):
        return AccountState(kind, why, google, passwords, form_error)

    if any(re.fullmatch(r"choose an account", t, re.IGNORECASE) for t in texts):
        # Google's own account picker: part of a Google sign-in already under way, not the employer's page.
        return AccountState(NONE, "Google's account picker")

    for kind, pattern in ((LOCKED, _LOCKED), (WRONG_PASSWORD, _WRONG_PASSWORD), (ACCOUNT_EXISTS, _EXISTS)):
        said = _first(pattern, texts)
        if said and (passwords or on_account_step or _first(_SIGN_IN, texts) or _first(_CREATE, texts)):
            return state(kind, said)
    code_box = re.search(r'- textbox "[^"]*(code|passcode|otp)[^"]*"', snapshot or "", re.IGNORECASE)
    if code_box and _first(_CODE, texts):
        return state(CODE_ENTRY, _first(_CODE, texts))
    said = _first(_VERIFY, texts)
    if said and not passwords:
        return state(VERIFY_EMAIL, said)
    if step and int(step.group(1)) >= 1 and not passwords and not on_account_step_is_current(snapshot):
        return state(SIGNED_IN, step.group(0))
    if passwords >= 2:
        return state(CREATE_FORM, _first(_CREATE, texts))
    if passwords == 1:
        return state(SIGN_IN_FORM, _first(_SIGN_IN, texts))
    ways = [t for t in texts if _WAYS_IN.search(t)]
    if len(ways) >= 2 or (ways and google):
        return state(CHOOSER, "; ".join(dict.fromkeys(ways))[:120])
    if _EMAIL_BOX.search(snapshot or "") and _first(_SIGN_IN, texts) and not _application_fields(snapshot):
        return state(EMAIL_FIRST, _first(_SIGN_IN, texts))
    if on_account_step:
        return state(LOADING, "the account step, with no form yet")
    return state(NONE)


def on_account_step_is_current(snapshot: str) -> bool:
    """Workday's step list names the account step as the current one ('current step 1 of 7' beside
    'Create Account/Sign In'), which is not being signed in."""
    for match in _STEP.finditer(snapshot or ""):
        nearby = (snapshot or "")[match.end(): match.end() + 160]
        if _ACCOUNT_STEP.search(nearby):
            return True
    return False


def _application_fields(snapshot: str) -> bool:
    # 'Email address' is a sign-in box, not a street address.
    return bool(re.search(r'- (textbox|combobox) "[^"]*(first name|last name|phone|street address|address line|'
                          r'home address|resume)', snapshot or "", re.IGNORECASE))


# ---------------------------------------------------------------------------
# What to do about it
# ---------------------------------------------------------------------------
GOOGLE = "google"
CREATE = "create"
SIGN_IN = "sign_in"
GIVE_EMAIL = "give_email"
ENTER_CODE = "enter_code"
RESET_PASSWORD = "reset_password"    # a known account refused its password: the owner's rule resets it
OPEN_SIGN_IN = "open_sign_in"        # a create form, but this email already has an account here
WAIT = "wait"                        # read the page again: it is still drawing
FOR_OWNER = "for_owner"              # the owner has to act; `why` says what
NOTHING = "nothing"                  # not an account page, or done


@dataclass
class Memory:
    """What the agent has already tried on this site in this run."""
    google_tried: bool = False
    google_refused: bool = False
    created: bool = False
    signed_in_tried: bool = False
    email_given: bool = False
    account_exists: bool = False
    reset_tried: bool = False        # a reset to the ATS password was already asked for on this site
    refused_before: bool = False     # login_guard: this account's last sign-in here was refused (kept across runs)
    held: str = ""                   # login_guard's reason for holding back, if any


@dataclass(frozen=True)
class Step:
    action: str
    why: str = ""


def _knows_account(memory: Memory) -> bool:
    """The site knows this account: it is on record, or the site took the email and asked for its password."""
    return memory.account_exists or memory.email_given


def next_step(state: AccountState, memory: Memory) -> Step:
    """The one decision table for the account step."""
    kind = state.kind
    if kind in (SIGNED_IN, NONE):
        return Step(NOTHING)
    if kind == LOADING:
        return Step(WAIT, "the account step has not drawn its form yet")
    if kind == LOCKED:
        return Step(FOR_OWNER, f"the site says: {state.why}. Unlock or reset it on the site, then press Continue")
    if kind == VERIFY_EMAIL:
        return Step(FOR_OWNER, "the site sent a verification email: open it, click its link, then press Continue")
    if kind == CODE_ENTRY:
        return Step(ENTER_CODE, "a code was emailed")
    if kind == WRONG_PASSWORD:
        # The owner's rule (CLAUDE.md §5): an account the site knows -- on record, or the site took the email and
        # asked for its password -- whose password it refused is reset to the same ATS password with the code
        # emailed to the owner, once per site per run. 'Wrong email or password' alone does not say the account
        # exists, so an unknown one stays the owner's. (Mutual of Enumclaw, iCIMS, 29 September.)
        if _knows_account(memory) and not memory.reset_tried:
            return Step(RESET_PASSWORD, "the site knows this account and refused its password: resetting it to the "
                                        "ATS password with the code emailed to you")
        return Step(FOR_OWNER, f"the site refused the email and password ({state.why}). Check them on the site "
                               f"(or reset the password to the one in Settings), then press Continue")
    if state.google_offered and not memory.google_tried and not memory.google_refused:
        return Step(GOOGLE, "the site offers Google sign-in")
    if kind == SIGN_IN_FORM and memory.refused_before:
        # The same refusal, remembered from an earlier run: the password is never typed again (every wrong one
        # counts towards a lock); the owner's rule resets it instead. (Mutual of Enumclaw, 29 September, 18:54: the
        # run stopped at the password page with 'sign-in paused' and did neither.)
        if _knows_account(memory) and not memory.reset_tried:
            return Step(RESET_PASSWORD, "this account's password was refused here before: resetting it to the "
                                        "ATS password with the code emailed to you")
        return Step(FOR_OWNER, "this account's password was refused here before"
                               + (" and the reset did not go through" if memory.reset_tried else "")
                               + ": set it on the site to the password in Settings (or check the account), "
                                 "then press Continue")
    if memory.held and kind in (CREATE_FORM, SIGN_IN_FORM, ACCOUNT_EXISTS):
        return Step(FOR_OWNER, memory.held)
    if kind == ACCOUNT_EXISTS:
        return Step(OPEN_SIGN_IN if not memory.signed_in_tried else FOR_OWNER,
                    "this email already has an account here" if not memory.signed_in_tried else
                    "this email has an account here and signing in did not work: check it on the site, then Continue")
    if kind == CREATE_FORM:
        if state.form_error:
            # The site wants the form finished (a box it points at): that is the form to fill, whatever the
            # record of an account says; login_guard still limits the tries.
            return Step(CREATE, f"the form says: {state.form_error}")
        if memory.account_exists:
            return Step(OPEN_SIGN_IN, "an account already exists for this email here")
        if memory.created:
            return Step(FOR_OWNER, "the new-account form is still showing after the account was created: "
                                   "look at the page, then press Continue")
        return Step(CREATE, "no account here yet")
    if kind == SIGN_IN_FORM:
        if memory.signed_in_tried:
            return Step(FOR_OWNER, "signing in did not get past the sign-in form: look at the page, then Continue")
        return Step(SIGN_IN)
    if kind == EMAIL_FIRST:
        return Step(NOTHING) if memory.email_given else Step(GIVE_EMAIL)
    if kind == CHOOSER:
        return Step(NOTHING, "a chooser without Google: the plan picks the way in")
    return Step(NOTHING)
