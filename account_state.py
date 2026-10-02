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
# The site says the account itself is not verified yet -- a message that sits ON a sign-in form, so it counts whatever
# boxes the form has (unlike _VERIFY, whose hint wording also appears on ordinary forms). Ciena on Workday,
# 30 September: "Verify your account before you sign in or request a verification email" on the Sign In form was
# read as a plain sign-in form, and the owner was told the password had been refused.
_UNVERIFIED = re.compile(
    r"verify your (account|email|e-mail)( address)? (before|to) (you )?(sign|log) ?in|request a (new )?verification "
    r"(email|e-mail|link)|(account|email|e-mail)( address)? (is |has )?(not|n't) (been |yet )*(verified|activated|"
    r"confirmed)|unverified (account|email)|account (may |might )?needs? (to be )?(verified|verification|activated)",
    re.IGNORECASE)
# A heading that says the account it offers is optional.
_OPTIONAL_ACCOUNT = re.compile(r"- heading[^\n]*\baccount\b[^\n]*\boptional\b|- heading[^\n]*\boptional\b[^\n]*\baccount\b",
                               re.IGNORECASE)
_CREATE = re.compile(r"\bcreate (an |your )?account\b|\bregister\b|\bsign ?up\b", re.IGNORECASE)
_SIGN_IN = re.compile(r"\bsign ?in\b|\blog ?in\b", re.IGNORECASE)
_WAYS_IN = re.compile(r"(?:sign|log)[\s-]?(?:in|on|up) (?:with|using|via) (google|email|linkedin|apple|microsoft|"
                      r"facebook)|continue with (google|email)|"
                      r"apply manually|use my last application|autofill with resume", re.IGNORECASE)
# The form was sent and the site pointed at a box: the form is still to be finished, whatever else is known.
_FORM_ERROR = re.compile(
    r"passwords? (do not|don't|must) match|please check (the )?box|field is required|is a required field|"
    r"(email|password).{0,30}(invalid|required|must contain|too short)", re.IGNORECASE)
# Every way a site words its Google sign-in: "Sign in with Google", "Login with Google" (Adzuna, 30 September: the
# one-word "Login" was not read as a Google sign-in, and the run tried the site's own password form instead),
# "Sign-in using Google", "Sign up with Google", "Continue with Google", "Google sign-in". The page agent finds the
# button with the same words.
GOOGLE_SIGN_IN = re.compile(r"\b(?:sign|log)[\s-]?(?:in|on|up)\s+(?:with|using|via|through)\s+google\b|"
                            r"\bcontinue\s+(?:with|using)\s+google\b|\bgoogle\s+sign[\s-]?(?:in|on)\b", re.IGNORECASE)
_GOOGLE = GOOGLE_SIGN_IN
_ACCOUNT_STEP = re.compile(r"create account\s*/\s*sign in|sign in\s*/\s*create account", re.IGNORECASE)


@dataclass(frozen=True)
class AccountState:
    kind: str
    why: str = ""                    # the words on the page that decided it
    google_offered: bool = False
    password_boxes: int = 0
    form_error: str = ""             # what the site says is wrong with the form it is showing
    can_create: bool = False         # a sign-in page that also offers 'Create Account'

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

    can_create = bool(re.search(r'- (?:button|link) "\s*(?:create (?:an |a new |your )?account|register|sign up)\s*"',
                                snapshot or "", re.IGNORECASE))

    def state(kind, why=""):
        return AccountState(kind, why, google, passwords, form_error, can_create)

    if any(re.fullmatch(r"choose an account", t, re.IGNORECASE) for t in texts):
        # Google's own account picker: part of a Google sign-in already under way, not the employer's page.
        return AccountState(NONE, "Google's account picker")

    # A box for an emailed code makes it a code step, whatever else the page says: "Your account is not verified.
    # Enter the verification code we sent" is verified with the code, not with a link.
    code_box = re.search(r'- textbox "[^"]*(code|passcode|otp)[^"]*"', snapshot or "", re.IGNORECASE)
    wants_code = bool(code_box and _first(_CODE, texts))
    for kind, pattern in ((LOCKED, _LOCKED), (VERIFY_EMAIL, _UNVERIFIED), (WRONG_PASSWORD, _WRONG_PASSWORD),
                          (ACCOUNT_EXISTS, _EXISTS)):
        if kind == VERIFY_EMAIL and wants_code:
            continue
        said = _first(pattern, texts)
        if said and (passwords or on_account_step or _first(_SIGN_IN, texts) or _first(_CREATE, texts)):
            return state(kind, said)
    if wants_code:
        return state(CODE_ENTRY, _first(_CODE, texts))
    said = _first(_VERIFY, texts)
    if said and not passwords:
        return state(VERIFY_EMAIL, said)
    if step and int(step.group(1)) >= 1 and not passwords and not on_account_step_is_current(snapshot):
        return state(SIGNED_IN, step.group(0))
    # An account the page itself calls optional, under the application ("Create a Career Profile account
    # (optional)" below Meta's Resume upload and Self ID; "the system will create your account after you submit"):
    # the page is the application, not an account step. Read as a new-account form, Meta's run tried to make the
    # account, then stopped for the owner twice, "the new-account form is still showing" (30 September).
    # Decided per box, by the section each sits in (field_requirements): only when every secret box on the page
    # is inside an optional section is the page the application. A required sign-in elsewhere on the same page
    # still makes it an account step -- a heading anywhere used to let every password on the page off.
    if passwords and _OPTIONAL_ACCOUNT.search(snapshot or ""):
        import field_requirements
        optional_section = field_requirements.optional_section_of(snapshot)
        if optional_section:
            return state(NONE, optional_section[:80])
        passwords = field_requirements.secret_boxes_in_use(snapshot) or passwords
    if passwords >= 2:
        return state(CREATE_FORM, _first(_CREATE, texts))
    headings = [_unescape(h) for h in re.findall(r'- heading "((?:[^"\\]|\\.)*)"', snapshot or "")]
    if passwords == 1 and says_create(headings):
        # One password box, no retype -- UKG's "Create your account" (30 September): the heading says what it is.
        return state(CREATE_FORM, next(h for h in headings if _CREATE.search(h)))
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


def _unescape(text: str) -> str:
    return text.replace('\\"', '"').replace("\\\\", "\\")


def says_create(headings) -> bool:
    """Whether a page's headings say it makes a new account ("Create your account", "Sign up", "Register") -- the
    heading, not a link: a sign-in page also links to "Sign up". One rule for the snapshot and for the live page."""
    headings = [" ".join(str(h or "").split()) for h in headings or ()]
    return any(_CREATE.search(h) for h in headings) \
        and not any(_SIGN_IN.search(h) and not _CREATE.search(h) for h in headings)


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
VERIFY_BY_LINK = "verify_by_link"    # open the site's account-verification link from the owner's Gmail
RESET_PASSWORD = "reset_password"    # a known account refused its password: the owner's rule resets it
OPEN_SIGN_IN = "open_sign_in"        # a create form, but this email already has an account here
OPEN_CREATE = "open_create"         # a sign-in page that offers 'Create Account': create first (owner, 1 October)
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
    account_active: bool = False     # signed in to successfully before: creating it again cannot succeed
    reset_tried: bool = False        # a reset to the ATS password was already asked for on this site
    refused_before: bool = False     # login_guard: this account's last sign-in here was refused (kept across runs)
    verify_tried: bool = False       # the verification link was already looked for on this site in this run
    held: str = ""                   # login_guard's reason for holding back, if any
    shared_portal: str = ""          # the portal whose one account covers every employer on it (sites.accounts)


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
        # The owner's decision of 30 September 2026: the agent opens the verification link the site emailed, from the
        # owner's Gmail, once per site per run (emailed_codes decides whether the mail may be read and which link).
        if not memory.verify_tried:
            return Step(VERIFY_BY_LINK, "the site sent a verification email: opening its link from your Gmail")
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
        if state.can_create and not memory.created:
            # Whether an account exists here is the site's to say: creating it either makes it or meets "already
            # exists", which makes it a known account whose password the owner's rule resets (owner, 2 October).
            return Step(OPEN_CREATE, "the site refused the password for an account the agent has no record of: "
                                     "creating it, and the site will say if it already exists")
        return Step(FOR_OWNER, f"the site refused the email and password ({state.why})"
                               + (" and the reset did not go through" if memory.reset_tried else "")
                               + ". Check them on the site, then press Continue")
    if state.google_offered and not memory.google_tried and not memory.google_refused:
        return Step(GOOGLE, "the site offers Google sign-in")
    if kind == SIGN_IN_FORM and memory.refused_before:
        # The same refusal, remembered from an earlier run: the password is never typed again (every wrong one
        # counts towards a lock); the owner's rule resets it instead. (Mutual of Enumclaw, 29 September, 18:54: the
        # run stopped at the password page with 'sign-in paused' and did neither.)
        if _knows_account(memory) and not memory.reset_tried:
            return Step(RESET_PASSWORD, "this account's password was refused here before: resetting it to the "
                                        "ATS password with the code emailed to you")
        if state.can_create and not memory.created:
            return Step(OPEN_CREATE, "this password was refused here before and no account is on record: creating "
                                     "it, and the site will say if it already exists")
        return Step(FOR_OWNER, "this account's password was refused here before"
                               + (" and the reset did not go through" if memory.reset_tried else "")
                               + ": set it on the site to the password in Settings (or check the account), "
                                 "then press Continue")
    if memory.held and kind in (CREATE_FORM, SIGN_IN_FORM, ACCOUNT_EXISTS):
        return Step(FOR_OWNER, memory.held)
    if kind == ACCOUNT_EXISTS:
        if not memory.signed_in_tried:
            return Step(OPEN_SIGN_IN, "this email already has an account here")
        if not memory.reset_tried:
            # The site says the account exists and refused its password: the owner's rule resets it to the ATS
            # password with the code emailed to you (CLAUDE.md section 5).
            return Step(RESET_PASSWORD, "this email has an account here and its password was refused: resetting it "
                                        "to the ATS password with the code emailed to you")
        return Step(FOR_OWNER, "this email has an account here, signing in was refused and the reset did not go "
                               "through: check it on the site, then press Continue")
    if kind == CREATE_FORM:
        if state.form_error:
            # The site wants the form finished (a box it points at): that is the form to fill, whatever the
            # record of an account says; login_guard still limits the tries.
            return Step(CREATE, f"the form says: {state.form_error}")
        # Create first (owner, 1 October): an unknown account, or one made but never signed in to -- if it exists the
        # site says so (ACCOUNT_EXISTS) and the agent signs in. An account signed in to before is signed in to:
        # creating it again cannot succeed and spends one of the day's two creations.
        if memory.account_active:
            return Step(OPEN_SIGN_IN, "the agent has signed in to this account before")
        if memory.shared_portal and not memory.signed_in_tried:
            # One account for every employer on this portal: it most likely exists from an earlier application.
            return Step(OPEN_SIGN_IN, f"one {memory.shared_portal} account covers every employer: signing in first")
        if memory.created:
            return Step(FOR_OWNER, "the new-account form is still showing after the account was created: "
                                   "look at the page, then press Continue")
        return Step(CREATE, "no account here yet")
    if kind == SIGN_IN_FORM:
        if state.can_create and not memory.created and not memory.signed_in_tried and not memory.account_active \
                and not memory.shared_portal:
            return Step(OPEN_CREATE, "create the account first; if it exists the site will say so")
        if memory.signed_in_tried:
            return Step(FOR_OWNER, "signing in did not get past the sign-in form: look at the page, then Continue")
        return Step(SIGN_IN)
    if kind == EMAIL_FIRST:
        return Step(NOTHING) if memory.email_given else Step(GIVE_EMAIL)
    if kind == CHOOSER:
        return Step(NOTHING, "a chooser without Google: the plan picks the way in")
    return Step(NOTHING)
