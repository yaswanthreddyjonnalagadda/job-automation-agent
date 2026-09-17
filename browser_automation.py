"""
Browser assistant built on Playwright.

Deliberate boundaries (do not remove these):
  * Scripted login is HARD-BLOCKED for linkedin.com, indeed.com, and
    dice.com (see BLOCKED_LOGIN_DOMAINS) -- those three explicitly ban
    automated account access in their ToS and actively detect/ban it.
    Auto-login is only attempted for other domains (employer ATS sites
    like Workday/Greenhouse/Lever/iCIMS), using credentials the user
    supplies for that account, and only if ATS credentials are configured.
  * No silent auto-submit, ever. The assistant fills in fields and drafts
    screening-question answers, then stops for an explicit human go/no-go
    (via a review-summary file + signal file, since this runs without a
    live terminal attached). The agent never clicks Submit at all.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

from playwright.sync_api import BrowserContext, Page, sync_playwright

import safety
from config import AppConfig, UserProfile
from sites import adapter_for

logger = logging.getLogger(__name__)

# Automated login is never attempted against these -- their ToS explicitly
# prohibits automated account access and they actively detect/ban it.
# Kept for callers that import it; the policy itself lives in safety.py.
BLOCKED_LOGIN_DOMAINS = safety.BLOCKED_PASSWORD_DOMAINS


class BlockedLoginDomainError(RuntimeError):
    pass


# Maps our known profile fields to likely form field identifiers (name/id/
# placeholder/label substrings, lowercased). Extend as you encounter new ATS
# platforms (Workday, Greenhouse, Lever, iCIMS, etc.).
# Order matters: _match_field returns the FIRST match, so more specific
# hints (first/middle/last name, exact "phone number") must come before
# broader ones (bare "name", bare "phone") or they'll shadow each other --
# e.g. a generic "name" hint would wrongly match "First Name" too.
_FIELD_HINTS: dict[str, list[str]] = {
    "prefix": ["prefix", "title (mr", "salutation"],
    "first_name": ["first name", "firstname", "fname"],
    "middle_name": ["middle name", "middlename"],
    # NOTE: no bare "lname" here -- it collides as a substring with
    # "schoolName" (schoo-LNAME), which put the candidate's surname into
    # Education's School field.
    "last_name": ["last name", "lastname", "surname"],
    "email": ["email"],
    # Bare "phone" is needed as well as the longer forms: Greenhouse labels the
    # field simply "Phone", and requiring "phone number" left it blank.
    # Phone fields with type detection: mobile/cell, home, work
    # These must come BEFORE the generic "phone" fallback
    "phone_mobile": ["mobile phone", "mobile number", "cell phone", "cellular phone", "cell number",
                     "mobilephone", "cellphone", "mobile (phone)?number"],
    "phone_home": ["home phone", "home number", "home telephone", "residential phone", "homephone"],
    "phone_work": ["work phone", "work number", "office phone", "business phone", "workphone"],
    "phone": ["phone number", "mobile number", "telephone number", "phone", "telephone"],
    "address_line1": ["address line 1", "street address", "address 1"],
    "city": ["city"],
    # "county" is safe alongside "country" -- neither contains the other.
    "county": ["county", "regionsubdivision"],
    "state": ["state", "province"],
    "postal_code": ["postal code", "zip code", "zipcode", "zip"],
    "location": ["location", "current location"],
    "linkedin_url": ["linkedin"],
    "portfolio_url": ["portfolio", "website", "personal site"],
    # Deliberately NO bare "name" hint here: internal field identifiers like
    # "companyName" or "schoolName" contain "name" as a substring, and a
    # bare hint wrongly matches those and overwrites them with the
    # candidate's own name instead of leaving them alone.
    # "_systemfield_name" is Ashby's fixed id for its plain "Name" field --
    # specific enough to be safe where a bare "name" is not.
    "full_name": ["full name", "your name", "applicant name", "_systemfield_name"],
}

# Word-boundary matching, not raw substring: a bare "state" hint matched
# inside "united states" ("are you legally authorized to work in the united
# states?") and nearly filled a work-authorization question with "Virginia".
# \bstate\b requires 'state' as a whole word, which "states" is not -- and
# this still correctly misses camelCase ids like "companyName" (lowercased to
# "companyname"), since there's no word boundary between "company" and "name"
# with no separator between them. One general fix for the whole bug class
# instead of removing hints one collision at a time.
_FIELD_HINT_PATTERNS: dict[str, list[re.Pattern]] = {
    key: [re.compile(rf"\b{re.escape(hint)}\b") for hint in hints]
    for key, hints in _FIELD_HINTS.items()
}


@dataclass
class DetectedField:
    selector: str
    label_text: str
    input_type: str
    matched_profile_key: Optional[str] = None


@dataclass
class DetectedQuestion:
    """A form field that isn't one of our known profile fields -- almost
    always an employer-specific screening/technical question."""
    question_text: str
    input_type: str  # "textarea" | "select" | "radio"
    selector: str  # for textarea/select: a CSS selector; for radio: the group's `name`
    options: list[str] = field(default_factory=list)


class FramedPage:
    """A browser tab whose application lives inside one of its frames.

    iCIMS (Charles Schwab) shows the posting, the sign-in and every step of the
    form inside a frame on its own page, and sends a frame opened on its own
    straight back to that outer page. So the agent works inside the frame:
    reading, typing and clicking go to the frame; the keyboard, screenshots,
    tabs and file choosers belong to the tab. The frame is looked up again
    whenever the site replaces it, as it does on every step.
    """

    _TAB_ONLY = frozenset({
        "keyboard", "mouse", "context", "screenshot", "is_closed", "bring_to_front", "reload", "frames",
        "main_frame", "on", "once", "remove_listener", "expect_file_chooser", "expect_popup",
        "expect_navigation", "expect_event", "close", "video", "viewport_size", "set_viewport_size",
        "route", "unroute", "pdf", "emulate_media", "add_init_script", "set_default_timeout",
    })

    def __init__(self, page, frame):
        self.top = page
        self._frame = frame
        self._name = frame.name or ""
        try:
            self._element_id = frame.frame_element().get_attribute("id") or ""
        except Exception:
            self._element_id = ""

    def frame(self):
        frame = self._frame
        try:
            if frame is not None and not frame.is_detached():
                return frame
        except Exception:
            pass
        try:
            for candidate in self.top.frames[1:]:
                if self._name and candidate.name == self._name:
                    self._frame = candidate
                    return candidate
            if self._element_id:
                handle = self.top.query_selector(f"iframe[id={json.dumps(self._element_id)}]")
                candidate = handle.content_frame() if handle else None
                if candidate is not None:
                    self._frame = candidate
                    return candidate
        except Exception:
            pass
        return self.top.main_frame  # the site has left the frame (a Google sign-in, say)

    @property
    def url(self) -> str:
        frame = self.frame()
        return self.top.url if frame == self.top.main_frame else frame.url

    def goto(self, url, **kwargs):
        return self.top.goto(url, **kwargs)

    def __getattr__(self, name):
        if name in FramedPage._TAB_ONLY:
            return getattr(self.top, name)
        frame = self.frame()
        if hasattr(frame, name):
            return getattr(frame, name)
        return getattr(self.top, name)


class JobApplicationAssistant:
    """One instance = one long-lived, visible browser session that persists
    login cookies between runs via a local user-data directory."""

    def __init__(self, config: AppConfig):
        self._config = config
        self._playwright = None
        self._context: Optional[BrowserContext] = None
        # Every value the agent puts on a page, so it never overwrites the user.
        self.values = safety.AgentValues()

    def _page_hint(self):
        """A stand-in page for adapter lookups when a helper was handed
        something other than a Page."""
        class _Fake:
            url = ""
        return _Fake()

    def adapter(self, page: Page):
        """The site adapter for the page in front of us (sites/)."""
        try:
            return adapter_for(page.url)
        except Exception:
            return adapter_for("")

    def __enter__(self) -> "JobApplicationAssistant":
        self._playwright = sync_playwright().start()
        profile_dir = Path(self._config.browser_profile_dir)
        # A run whose process is killed leaves its browser running, and that
        # browser keeps holding this profile: every later run then died at
        # startup with "Opening in existing browser session". The leftover
        # belongs to the agent -- it is identified by this profile directory,
        # never by being a browser -- so it is closed here.
        self._close_leftover_browsers(profile_dir)
        channel = self._choose_channel()
        try:
            self._context = self._launch(channel, profile_dir)
        except Exception as exc:
            if not channel:
                raise
            # Chrome is not installed, or it declined to start a second
            # instance after all. Its stub claims the profile on the way out,
            # so the leftovers go before the bundled build tries.
            logger.warning("Could not start %s (%s) -- falling back to the bundled browser",
                           channel, str(exc).splitlines()[0][:120])
            self._release_profile(profile_dir)
            self._context = self._launch("", profile_dir)
        return self

    def _choose_channel(self) -> str:
        """Force the configured browser channel (always use real Chrome, never fallback).

        IMPORTANT: This means real Chrome will be locked to the agent while 
        applications run. The user explicitly chose this behavior for better 
        rendering fidelity (real Chrome matches the browser they test with).
        
        Previous versions would fall back to bundled chromium if real Chrome
        was already running, to allow simultaneous browsing. That fallback
        is now disabled per user request.
        """
        # Always use the configured browser channel (e.g., "chrome")
        # Do NOT fall back to chromium even if Chrome is already running.
        # User wants real Chrome only for this use case.
        channel = (getattr(self._config, "browser_channel", "") or "").strip()
        
        # If no channel is configured, use real Chrome by default
        if not channel or channel == "chromium":
            return "chrome"
        
        logger.info("Using %s with the profile at %s (Chrome already running is OK)",
                   channel, self._config.browser_profile_dir)
        return channel

    @staticmethod
    def _chrome_is_running() -> bool:
        try:
            if os.name != "nt":
                return False
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                                 capture_output=True, text=True, timeout=20).stdout
            return "chrome.exe" in out
        except Exception:
            return False

    @staticmethod
    def _close_leftover_browsers(profile_dir: Path) -> int:
        """Ends browser processes still holding the agent's own profile.

        Matched on the profile path, so the user's own Chrome windows -- which
        use their own profile -- are never touched. The listing is done in the
        shell and the matching here: wmic is gone from Windows 11, and quoting
        a Windows path into a PowerShell filter is its own source of bugs.
        """
        if os.name != "nt":
            return 0
        try:
            listing = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=45).stdout
            rows = json.loads(listing or "[]")
        except Exception as exc:
            logger.debug("Could not list processes: %s", exc)
            return 0

        marker = str(profile_dir).lower()
        pids = [str(row.get("ProcessId")) for row in rows
                if marker in (row.get("CommandLine") or "").lower()]
        for pid in pids:
            subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True, check=False)
        if pids:
            logger.info("Closed %d browser process(es) left over from an earlier run", len(pids))
        return len(pids)

    @staticmethod
    def _release_profile(profile_dir: Path) -> None:
        """Clears the locks a failed launch leaves in the agent's own profile.

        Only ever this profile -- never the user's, whose locks mean a browser
        of theirs is genuinely open.
        """
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
            try:
                (profile_dir / name).unlink(missing_ok=True)
            except OSError as exc:
                logger.debug("Could not clear %s: %s", name, exc)

    def _launch(self, channel: str, profile_dir: Path):
        kwargs = {"channel": channel} if channel and channel != "chromium" else {}
        return self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=self._config.browser_headless,
            # Playwright leaves the sandbox off by default, which passes
            # --no-sandbox. Real Chrome then shows the user a banner saying
            # stability and security will suffer -- and it is right. The
            # agent browses real employer sites, so the sandbox stays on.
            chromium_sandbox=True,
            **kwargs,
            # Size the page to the real, maximized window. Playwright's default
            # fixed 1280x720 page is taller than the window can be on a
            # 1280x720 screen, so the bottom of every page sat below the
            # screen edge -- including CBTS's pinned "Submit application" bar,
            # which the user never saw.
            no_viewport=True,
            args=["--start-maximized"],
            # Deny browser-level permission requests (push notifications, location, etc.)
            # so they don't block the application mid-form
            permissions=[],  # Empty list = deny all permissions
            geolocation={"latitude": 0, "longitude": 0},  # If location is needed, provide dummy
        )

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # Teardown must not raise. Closing a browser the user already closed
        # themselves throws TargetClosedError, which turned a clean, finished
        # run into an alarming exit-code-1 traceback that looks exactly like a
        # real failure -- and would mask any genuine exception on its way out.
        if self._context:
            try:
                self._context.close()
            except Exception as exc:
                logger.debug("Browser context already closed: %s", exc)
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception as exc:
                logger.debug("Playwright already stopped: %s", exc)

    def with_retries(self, what: str, action, attempts: int = 0):
        """Runs a flaky page action again before giving up.

        Employer sites drop requests, re-render mid-click and occasionally
        hand back a blank frame; one retry usually settles it. Every failed
        attempt is logged so the audit trail shows what the page did.
        """
        attempts = attempts or getattr(self._config, "action_retries", 3)
        last = None
        for attempt in range(1, attempts + 1):
            try:
                return action()
            except Exception as exc:
                last = exc
                logger.warning("%s failed (attempt %d/%d): %s", what, attempt, attempts,
                               str(exc).splitlines()[0][:140])
                time.sleep(1.5 * attempt)
        logger.error("%s failed after %d attempts", what, attempts)
        if last:
            raise last

    def save_progress(self, page: Page) -> bool:
        """Clicks a form's own Save/Save draft button so a part-finished
        application survives a reload. Never a Submit button: safety decides
        what counts as one."""
        buttons = page.get_by_role("button", name=re.compile(r"^\s*(save( draft| and finish later| for later)?)\s*$",
                                                            re.IGNORECASE))
        for i in range(min(buttons.count(), 4)):
            button = buttons.nth(i)
            try:
                if not button.is_visible() or safety.is_submit_label(button.inner_text()):
                    continue
                if self._click_resiliently(button, timeout_ms=5_000):
                    page.wait_for_timeout(2_500)
                    logger.info("Saved the application's progress")
                    return True
            except Exception:
                continue
        return False

    def open_job_page(self, url: str) -> Page:
        assert self._context is not None, "Use within a `with` block"
        page = self._context.new_page()
        self.with_retries(f"opening {url[:60]}", lambda: page.goto(url, wait_until="domcontentloaded"))
        # Workday/Greenhouse/etc. render via JS after domcontentloaded fires,
        # so the page is typically still blank at this point. Give it a
        # bounded chance to settle; some sites never go fully idle (ongoing
        # analytics/polling), so don't let that hang the whole flow.
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            logger.info("Page did not reach networkidle within 10s, continuing anyway")
            page.wait_for_timeout(3_000)
        return page

    def wait_for_manual_login(self, page: Page, logged_in_selector: str, timeout_ms: int = 300_000) -> None:
        """Blocks until a selector that only appears when logged in shows up,
        giving the human time to sign in by hand in the visible browser."""
        logger.info("Waiting for manual login (looking for selector: %s)...", logged_in_selector)
        page.wait_for_selector(logged_in_selector, timeout=timeout_ms)
        logger.info("Login detected.")

    # Frames that are never the application form.
    _NON_FORM_FRAME_HOSTS = (
        "recaptcha", "google.com", "googleapis.com", "gstatic",
        "doubleclick", "facebook", "linkedin.com", "hotjar", "segment",
    )

    def focus_application_frame(self, page: Page) -> bool:
        """If the application form lives in an embedded iframe, navigate to it
        so it becomes the top-level document.

        Greenhouse (and several other ATSs) embed their form in an iframe on
        the employer's own careers page. Playwright selectors don't cross
        frame boundaries, so the page looks empty -- 0 fields, no upload
        input -- while a complete form sits inside. Navigating to the frame's
        own URL is simpler and far less fragile than threading a frame handle
        through every field-filling method."""
        try:
            for frame in page.frames:
                url = (frame.url or "").lower()
                if not url.startswith("http") or url == page.url.lower():
                    continue
                if any(host in url for host in self._NON_FORM_FRAME_HOSTS):
                    continue
                try:
                    has_file = frame.locator("input[type='file']").count() > 0
                    fields = frame.locator("input:not([type='hidden']), textarea, select").count()
                except Exception:
                    continue
                if has_file or fields >= 5:
                    logger.info(
                        "Application form is in an embedded frame (%d fields); "
                        "navigating to it directly: %s", fields, frame.url[:120],
                    )
                    page.goto(frame.url, wait_until="domcontentloaded", timeout=45_000)
                    page.wait_for_timeout(3_000)
                    return True
        except Exception as exc:
            logger.warning("Could not inspect frames: %s", exc)
        return False

    def dismiss_cookie_banner(self, page: Page) -> bool:
        """Accepts/closes a cookie consent banner. These overlay the page and
        swallow clicks aimed at whatever is underneath, which has already cost
        several 30-second actionability timeouts on other sites."""
        for selector in (
            # Rejecting optional cookies clears a banner just as well and shares
            # less; take that choice whenever the banner offers it.
            "button:has-text('Reject All Cookies')",
            "button:has-text('Reject All')",
            "button:text-is('Accept')",
            "button:has-text('Accept All')",
            "button:has-text('Accept Cookies')",
            "button:has-text('Allow all')",
            "button[id*='accept' i]",
        ):
            try:
                btn = page.locator(selector).first
                if btn.count() and btn.is_visible():
                    if self._click_resiliently(btn, timeout_ms=4_000):
                        page.wait_for_timeout(1_000)
                        logger.info("Dismissed cookie banner")
                        return True
            except Exception:
                continue
        return False

    def expand_all_sections(self, page: Page) -> None:
        """Opens collapsed form sections ("Expand all sections" on SuccessFactors
        applications). Collapsed, their fields are invisible and the agent
        found 0 of them on IGT's form."""
        try:
            control = page.locator("a, button, [role=button]").filter(
                has_text=re.compile(r"^\s*expand all( sections)?\s*$", re.IGNORECASE)
            )
            for i in range(min(control.count(), 3)):
                if control.nth(i).is_visible():
                    self._click_resiliently(control.nth(i), timeout_ms=4_000)
                    page.wait_for_timeout(2_000)
                    logger.info("Expanded all form sections")
                    return
        except Exception:
            pass

    def detect_form_fields(self, page: Page) -> list[DetectedField]:
        """Scans visible text/email/tel inputs and textareas, and tries to
        match each to a label using its aria-label, placeholder, name, id,
        or an associated <label> element."""
        self.expand_all_sections(page)
        detected: list[DetectedField] = []
        elements = page.query_selector_all(
            # type='url' included: Ashby renders LinkedIn/website fields that way.
            "input[type='text'], input[type='email'], input[type='tel'], input[type='url'], "
            "input:not([type]), textarea, select"
        )
        for el in elements:
            if not el.is_visible():
                continue
            label_text = " ".join(
                filter(
                    None,
                    [
                        el.get_attribute("aria-label"),
                        el.get_attribute("placeholder"),
                        el.get_attribute("name"),
                        el.get_attribute("id"),
                    ],
                )
            ).lower()
            label_text += " " + self._label_for(page, el).lower()

            if self._is_structured_entry_field(el):
                # Owned by fill_experience_section/fill_education_section,
                # which know which entry each value belongs to. The generic
                # matcher does not: it wrote the profile's home location into
                # every role's Location, and the first role's description into
                # every Role Description box.
                continue

            if self._is_honeypot(label_text):
                # Bot traps: a field whose own label says humans must leave it
                # blank. Filling one flags the application as automated, so it
                # is never a match no matter what profile key it looks like.
                logger.info("Skipping honeypot field: %s", label_text.strip()[:80])
                continue

            input_type = el.get_attribute("type") or el.evaluate("e => e.tagName.toLowerCase()")
            selector = self._build_selector(el)
            matched_key = self._match_field(label_text)
            if matched_key is None:
                # Schwab's phone box is labelled only "Number" and named
                # "css_phoneNumber"; its autofill hint, tel-national, is the
                # one thing that says what it is.
                hint = self._AUTOCOMPLETE_WORDS.get((el.get_attribute("autocomplete") or "").strip().lower())
                if hint:
                    matched_key = self._match_field(f"{label_text} {hint}")
            detected.append(
                DetectedField(
                    selector=selector,
                    label_text=label_text.strip(),
                    input_type=input_type,
                    matched_profile_key=matched_key,
                )
            )
        # Finding nothing usually means the form is inside an embedded frame
        # rather than that there's nothing to fill. Go there and re-scan once,
        # guarding against recursing if the frame is empty too.
        if not detected and not getattr(self, "_frame_hop_attempted", False):
            self._frame_hop_attempted = True
            self.dismiss_cookie_banner(page)
            if self.focus_application_frame(page):
                return self.detect_form_fields(page)

        logger.info("Detected %d fillable fields, matched %d to profile data",
                    len(detected), sum(1 for d in detected if d.matched_profile_key))
        return detected

    @staticmethod
    def _is_structured_entry_field(el) -> bool:
        """True for a field belonging to a repeated Work Experience or
        Education entry (ids like 'workExperience-3--location'). These are
        per-entry and must only ever be written by the structured-resume
        fillers, which track which entry is which."""
        try:
            ident = (el.get_attribute("id") or "") + " " + (el.get_attribute("data-automation-id") or "")
        except Exception:
            return False
        ident = ident.lower()
        return any(
            marker in ident
            for marker in ("workexperience-", "education-", "languages-", "certification-")
        )

    @staticmethod
    def _is_honeypot(label_text: str) -> bool:
        """True for anti-bot decoy fields, which announce themselves in their
        own label/aria-label text (Workday's is literally 'this input is for
        robots only, do not enter if you're human')."""
        return any(
            marker in label_text
            for marker in (
                "for robots only",
                "do not enter if you're human",
                "do not enter if you are human",
                "leave this field blank",
                "leave blank",
                "honeypot",
            )
        )

    @staticmethod
    def _label_for(page: Page, el) -> str:
        """Best-effort human-readable label for a form element: aria-label,
        placeholder, an associated <label for=id>, or the nearest
        <fieldset><legend> ancestor (common for radio-button questions)."""
        parts = [el.get_attribute("aria-label") or "", el.get_attribute("placeholder") or ""]
        el_id = el.get_attribute("id")
        if el_id:
            label_el = page.query_selector(f"label[for='{el_id}']")
            if label_el:
                parts.append(label_el.inner_text() or "")
        text = " ".join(p.strip() for p in parts if p.strip())
        if not text:
            try:
                legend_text = el.evaluate(
                    "el => el.closest('fieldset')?.querySelector('legend')?.innerText || ''"
                )
                text = (legend_text or "").strip()
            except Exception:
                pass
        return text

    @staticmethod
    def _group_question_text(radio_el) -> str:
        """The actual question for a radio-button GROUP -- deliberately
        distinct from _label_for, which would return an individual option's
        own label (e.g. 'Yes') if asked about one radio input. Looks at the
        group's container (fieldset/legend, or an ARIA radiogroup with
        aria-labelledby/aria-label), never at a single option's label."""
        try:
            text = radio_el.evaluate(
                """el => {
                    const container = el.closest("[role='radiogroup'], fieldset");
                    if (!container) return '';
                    const labelledBy = container.getAttribute('aria-labelledby');
                    if (labelledBy) {
                        const labelEl = document.getElementById(labelledBy);
                        if (labelEl && labelEl.innerText.trim()) return labelEl.innerText;
                    }
                    const legend = container.querySelector('legend');
                    if (legend && legend.innerText.trim()) return legend.innerText;
                    const ariaLabel = container.getAttribute('aria-label');
                    if (ariaLabel) return ariaLabel;
                    return '';
                }"""
            )
            return (text or "").strip()
        except Exception:
            return ""

    # Fields about OTHER people or conditional follow-ups. IGT's "If yes, please
    # provide the name of the relative" got the candidate's own name, and a
    # "...relative or close associate of a state official" question was
    # matched to State.
    _NOT_ABOUT_CANDIDATE = re.compile(
        r"relative|spouse|family member|associate|reference|referr|emergency|supervisor|manager's|"
        r"\bif yes\b|\bif so\b|are you|have you|do you|were you|name of (the|your) (relative|employee|contact)",
        re.IGNORECASE,
    )

    # The standard autofill hints (the autocomplete attribute) that name a
    # profile field outright.
    _AUTOCOMPLETE_WORDS = {
        "email": "email", "tel": "phone", "tel-national": "phone",
        "given-name": "first name", "family-name": "last name", "postal-code": "postal code",
    }

    @staticmethod
    def _match_field(label_text: str) -> Optional[str]:
        if JobApplicationAssistant._NOT_ABOUT_CANDIDATE.search(label_text):
            return None
        for key, patterns in _FIELD_HINT_PATTERNS.items():
            if any(p.search(label_text) for p in patterns):
                if key == "state" and len(label_text) > 90:
                    continue  # a long question that merely mentions a state
                return key
        return None

    @staticmethod
    def _build_selector(el) -> str:
        # Attribute selectors, not '#id': Ashby ids are UUIDs, and one that
        # starts with a digit ('#254815db-...') is invalid CSS, so the phone
        # field threw a SyntaxError and was left blank. json.dumps quotes and
        # escapes the value safely.
        el_id = el.get_attribute("id")
        if el_id:
            return f"[id={json.dumps(el_id)}]"
        name = el.get_attribute("name")
        if name:
            return f"[name={json.dumps(name)}]"
        return ""  # caller should skip fields with no stable selector

    def set_value(self, page: Page, selector: str, value: str, what: str = "", source: str = "") -> bool:
        """The only way this class writes text into a form.

        Refuses to touch a signature or attestation field, refuses to overwrite
        anything the agent did not write itself, and records what it wrote so a
        later pass can tell its own answer from the user's.
        """
        if safety.is_attestation(what):
            logger.info("LEFT_FOR_YOU: %r is a signature/attestation -- the agent never fills it", what[:70])
            return False
        try:
            field = page.locator(selector).first
            current = field.input_value(timeout=3_000) or ""
            if not self.values.may_write(page, selector, current):
                logger.info("Keeping your answer in %s (%r)", what or selector, current[:40])
                return False
            if not field.is_editable(timeout=2_000):
                logger.info("Skipping locked field %s (value %r)", what or selector, current[:40])
                return False
            field.fill(value, timeout=8_000)
            # A key press plus leaving the field makes React-style forms
            # (Eightfold) register the value; fill() alone left CBTS validating
            # a filled box as blank.
            try:
                field.press("End")
                field.press("Tab")
            except Exception:
                pass
            self.values.record(page, selector, value, source or "agent")
            self.note_page_changed()
            return True
        except Exception as exc:
            logger.warning("Could not fill %s: %s", what or selector, str(exc).splitlines()[0][:120])
            return False

    # The two-letter code a phone widget uses for the profile's country.
    _DIAL_COUNTRY = {"+1": ("us", "United States"), "+44": ("gb", "United Kingdom"),
                     "+91": ("in", "India"), "+61": ("au", "Australia")}

    def set_phone_country(self, page: Page, profile) -> int:
        """Chooses the phone number's country wherever a form asks for it.

        RZR Global's form (Greenhouse) asks twice: a "Country" dropdown beside
        the number, and a flag button inside the number box. Both were left
        unset -- the dropdown showed no options until something was typed, and
        the flag button was never recognised -- so the form refused with
        "Select a country". Only a country not yet chosen is set.
        """
        code = (getattr(profile, "phone_country_code", "") or "+1").strip()
        iso, country = self._DIAL_COUNTRY.get(code, ("us", getattr(profile, "country", "United States")))
        done = 0

        # 1. intl-tel-input: a flag button that opens a searchable list.
        buttons = page.locator("button.iti__selected-country, .iti__selected-flag[role=combobox]")
        for i in range(min(buttons.count(), 4)):
            button = buttons.nth(i)
            try:
                if not button.is_visible():
                    continue
                current = (button.get_attribute("title") or button.get_attribute("aria-label") or "").lower()
                if country.lower() in current:
                    continue  # already the right country
                button.click(timeout=4_000)
                page.wait_for_timeout(500)
                panel_id = button.get_attribute("aria-controls") or ""
                scope = page.locator(f"[id={json.dumps(panel_id)}]") if panel_id else page
                search = scope.locator("input.iti__search-input, input[type=search]").first
                if search.count():
                    search.fill(country)
                    page.wait_for_timeout(500)
                option = scope.locator(f".iti__country[data-country-code={json.dumps(iso)}]").first
                if option.count():
                    option.scroll_into_view_if_needed(timeout=3_000)
                    option.click(timeout=4_000)
                    page.wait_for_timeout(400)
                    try:
                        page.keyboard.press("Tab")
                    except Exception:
                        pass
                    now = (button.get_attribute("title") or button.get_attribute("aria-label") or "")
                    if country.lower() in now.lower():
                        logger.info("PROFILE_ANSWER: phone country -> %r", now.strip()[:40])
                        done += 1
                else:
                    page.keyboard.press("Escape")
            except Exception as exc:
                logger.debug("Phone flag picker failed: %s", str(exc).splitlines()[0][:100])

        # 2. a searchable "Country" dropdown that belongs to the phone number.
        pickers = page.locator(".phone-input__country input[role=combobox], "
                               "fieldset.phone-input input[role=combobox]")
        for i in range(min(pickers.count(), 3)):
            field = pickers.nth(i)
            try:
                if not field.is_visible():
                    continue
                # Shown but not taken: the form still marks the field invalid
                # ("Select a country" under a dropdown showing +1), so the
                # choice is made again rather than trusted.
                invalid = (field.get_attribute("aria-invalid") or "").lower() == "true"
                if self.displayed_value(field) and not invalid:
                    continue
                self.open_picker_control(page, field)
                if invalid:
                    for _ in range(3):
                        field.press("Backspace")
                field.type(country, delay=30, timeout=5_000)
                page.wait_for_timeout(900)
                options = page.locator("[class*=select__option]:visible, [role=option]:visible")
                texts = [t.strip() for t in options.all_inner_texts()]
                index = self._best_option(texts, [country, f"{country} of America", f"{country} {code}"])
                if index is None:
                    page.keyboard.press("Escape")
                    continue
                options.nth(index).click(timeout=4_000)
                page.wait_for_timeout(400)
                # The form re-checks a field when you leave it; until then it
                # went on saying "Select a country" under a chosen country.
                try:
                    field.press("Tab")
                    page.wait_for_timeout(400)
                except Exception:
                    pass
                chosen = self.displayed_value(field)
                # The phone block is checked as a whole when its number field is
                # left: a chosen country went on showing "Select a country"
                # until then.
                try:
                    number = page.locator("fieldset.phone-input input[type=tel], fieldset.phone-input "
                                          "input:not([role=combobox]), input#phone").first
                    if number.count():
                        number.click(timeout=3_000)
                        number.press("End")
                        number.press("Tab")
                        page.wait_for_timeout(500)
                except Exception:
                    pass
                if chosen:
                    self.note_page_changed()
                    logger.info("PROFILE_ANSWER: phone country -> %r", chosen[:40])
                    done += 1
            except Exception as exc:
                logger.debug("Phone country dropdown failed: %s", str(exc).splitlines()[0][:100])

        # 3. any other "Country Code" combobox: a link that opens a searchable
        #    list, as on Schwab's sign-in step (iCIMS), options "(+1) United States".
        combos = page.locator("[role=combobox]:not(input):not(select)")
        for i in range(min(combos.count(), 8)):
            combo = combos.nth(i)
            try:
                if not combo.is_visible():
                    continue
                name = " ".join(((combo.get_attribute("aria-label") or "") + " " + combo.evaluate(
                    "e => { const c = e.closest('div, fieldset, li'); const l = c && c.parentElement"
                    " && c.parentElement.querySelector('label'); return l ? l.innerText : ''; }")).split())
                if not re.search(r"country\s*code|dial(ing)?\s*code|phone\s*country", name, re.IGNORECASE):
                    continue
                shown = " ".join((combo.inner_text() or "").split())
                if shown and not re.search(r"make a selection|^\W*select\b|choose|^\W*$", shown, re.IGNORECASE):
                    continue  # a country is already chosen -- by the site, the user or the agent
                combo.click(timeout=4_000)
                page.wait_for_timeout(500)
                panel_id = combo.get_attribute("aria-controls") or ""
                scope = page.locator(f"[id={json.dumps(panel_id)}]") if panel_id else page
                search = scope.locator("input:visible").first
                if search.count():
                    search.fill(country)
                    page.wait_for_timeout(600)
                options = scope.locator("[role=option]:visible")
                texts = [" ".join(t.split()) for t in options.all_inner_texts()]
                index = self._best_option(texts, [f"({code}) {country}", f"{country} ({code})", country])
                if index is None:
                    page.keyboard.press("Escape")
                    continue
                options.nth(index).click(timeout=4_000)
                page.wait_for_timeout(400)
                now = " ".join((combo.inner_text() or "").split())
                if country.lower() in now.lower():
                    self.note_page_changed()
                    logger.info("PROFILE_ANSWER: phone country code -> %r", now[:40])
                    done += 1
            except Exception as exc:
                logger.debug("Country code list failed: %s", str(exc).splitlines()[0][:100])
        return done

    def fix_rejected_phone_numbers(self, page: Page) -> int:
        """Rewrites a phone the form has just called invalid.

        Dayforce keeps the country code in a control of its own and wants the
        number as digits, so "(571) 354-5212" came back as "Home Phone number
        is invalid" -- on a field the agent had filled from the profile, with
        nothing on the page saying what shape it wanted.
        """
        try:
            complaints = page.evaluate("""() => {
                const visible = e => !!(e.offsetParent || e.getClientRects().length);
                return [...document.querySelectorAll('[role=alert], [class*=error i], [aria-invalid=true]')]
                    .filter(visible)
                    .map(e => (e.innerText || '').trim())
                    .filter(t => /phone/i.test(t) && /invalid|not valid|format/i.test(t));
            }""")
        except Exception:
            return 0
        if not complaints:
            return 0

        fixed = 0
        for box in page.query_selector_all("input[type=tel], input[id*=hone], input[name*=hone]"):
            try:
                if not box.is_visible():
                    continue
                value = (box.get_attribute("value") or box.input_value() or "").strip()
                digits = re.sub(r"\D", "", value)
                if not digits or digits == value:
                    continue
                # A country code lives in its own control here, so the number
                # goes in without one.
                if len(digits) == 11 and digits.startswith("1"):
                    digits = digits[1:]
                box.fill("")
                box.type(digits, delay=20)
                box.evaluate("e => e.blur()")
                page.wait_for_timeout(400)
                logger.info("Rewrote a phone number the form rejected: %r -> %r", value, digits)
                fixed += 1
            except Exception as exc:
                logger.debug("Could not rewrite a phone number: %s", str(exc).splitlines()[0][:100])
        return fixed

    def _current_value(self, page: Page, selector: str) -> str:
        """What a control holds right now, picker or plain input."""
        try:
            field = page.locator(selector).first
            return self.displayed_value(field) or ""
        except Exception:
            return ""

    def fill_detected_fields(
        self, page: Page, fields: list[DetectedField], profile: UserProfile
    ) -> list[DetectedField]:
        """Returns the fields that were ACTUALLY filled (non-empty value,
        no exception) -- not just matched. Callers building a review summary
        should use this return value, not the input `fields` list, or
        they'll report fields as filled that are still blank on screen."""
        # getattr with defaults on purpose: this module is hot-reloadable but
        # config.py is not, so a profile object created before a new field
        # was added would otherwise crash the whole run on attribute access.
        def p(name: str) -> str:
            return getattr(profile, name, "") or ""

        # A cookie banner left open covers the bottom of the page -- on IGT it
        # blocked the upload tiles and the last dropdown on every pass.
        self.dismiss_cookie_banner(page)
        # A privacy pop-up can arrive at any point -- ADP's appeared only after
        # Google sign-in finished -- and the form underneath cannot be used
        # until it is answered. Checked on every pass, not only after an upload.
        self.accept_consent_dialog(page)
        try:
            if self.complete_emailed_passcode(page):
                fields = self.detect_form_fields(page)
        except Exception as exc:
            logger.warning("Passcode step failed: %s", str(exc).splitlines()[0][:160])

        # Sign in first when the site offers it in its header: logging in can
        # reload the form, which would wipe anything filled before it.
        try:
            if self.sign_in_from_header(page, p("email")):
                fields = self.detect_form_fields(page)
        except Exception as exc:
            logger.warning("Header sign-in failed: %s", exc)

        values = {
            "prefix": p("prefix"),
            "full_name": p("full_name"),
            "first_name": p("full_name").split()[0] if p("full_name") else "",
            "last_name": p("full_name").split()[-1] if p("full_name") else "",
            "email": p("email"),
            "phone": p("phone"),
            "phone_mobile": p("phone_mobile") or p("phone"),  # Use phone_mobile if set, fallback to phone
            "phone_home": p("phone_home") or p("phone"),  # Use phone_home if set, fallback to phone
            "phone_work": p("phone_work") or p("phone"),  # Use phone_work if set, fallback to phone
            "address_line1": p("address_line1"),
            "city": p("city"),
            "county": p("county"),
            "state": p("state"),
            "postal_code": p("postal_code"),
            "location": p("current_location"),
            "linkedin_url": p("linkedin_url"),
            "portfolio_url": p("portfolio_url"),
        }
        actually_filled: list[DetectedField] = []
        for field in fields:
            if not field.matched_profile_key or not field.selector:
                continue
            value = values.get(field.matched_profile_key, "")
            if not value:
                continue
            try:
                # page.fill() only works on <input>/<textarea>/contenteditable --
                # a <select> (e.g. Prefix, State dropdowns) needs select_option()
                # or it silently fails.
                if field.input_type == "select":
                    select = page.locator(field.selector).first
                    if (select.input_value() or "").strip() and not self.values.is_ours(
                        page, field.selector, select.input_value()
                    ):
                        logger.info("Keeping your answer in %s", field.label_text[:50])
                        continue
                    page.select_option(field.selector, label=value)
                    self.values.record(page, field.selector, value, f"profile:{field.matched_profile_key}")
                    actually_filled.append(field)
                elif self.set_value(page, field.selector, value, field.label_text,
                                    source=f"profile:{field.matched_profile_key}"):
                    actually_filled.append(field)
            except Exception as exc:
                logger.warning("Could not fill field %s: %s", field.selector, exc)
        logger.info("Auto-filled %d/%d detected fields", len(actually_filled), len(fields))
        self.fix_rejected_phone_numbers(page)
        self.set_phone_country(page, profile)

        # A form that rebuilds itself after reading the resume (Dayforce) drops
        # what was typed into the version before it: address, postcode and
        # phone were filled and then blank again at hand-over. So look once
        # more, after the page has settled, and fill what is still empty.
        if actually_filled and not getattr(self, "_refilling", False):
            self._refilling = True
            try:
                page.wait_for_timeout(1_500)
                try:
                    page.wait_for_load_state("networkidle", timeout=5_000)
                except Exception:
                    pass
                blanks = [f for f in self.detect_form_fields(page)
                          if f.matched_profile_key and f.selector
                          and values.get(f.matched_profile_key)
                          and not (self._current_value(page, f.selector) or "").strip()]
                if blanks:
                    logger.info("Filling %d field(s) the page cleared while it rebuilt itself", len(blanks))
                    for again in self.fill_detected_fields(page, blanks, profile):
                        if again not in actually_filled:
                            actually_filled.append(again)
            except Exception as exc:
                logger.debug("Second fill pass failed: %s", str(exc).splitlines()[0][:120])
            finally:
                self._refilling = False
        self.handle_auth_gate(page, profile.email)
        self.apply_dropdown_answers(page)
        self._profile = profile
        try:
            self.answer_standard_questions(page, profile)
        except Exception as exc:
            logger.warning("Standard answers failed: %s", str(exc).splitlines()[0][:160])

        # A first sign-in can put a "Create your profile" window over the
        # application. The fields above were that window's; once it's saved,
        # fill the application form underneath.
        if self.complete_profile_dialog(page) and not getattr(self, "_refilling_after_profile", False):
            self._refilling_after_profile = True
            try:
                return self.fill_detected_fields(page, self.detect_form_fields(page), profile)
            finally:
                self._refilling_after_profile = False
        return actually_filled

    def complete_profile_dialog(self, page: Page) -> bool:
        """Saves a site's 'Create your profile' / 'Complete your profile'
        window (shown after a first Google or email sign-in) once its required
        fields are filled. The Submit clicked here is scoped to that window
        only, and it creates the candidate account profile -- never the job
        application, which stays the user's to submit."""
        try:
            dialogs = page.locator("[role=dialog], [aria-modal=true]").filter(
                has_text=re.compile(r"(create|complete) (your |a )?(candidate )?profile", re.IGNORECASE)
            )
            dialog = None
            for i in range(dialogs.count()):
                if dialogs.nth(i).is_visible():
                    dialog = dialogs.nth(i)
            if dialog is None:
                return False
            if re.search(r"submit (your )?application|apply now", dialog.inner_text() or "", re.IGNORECASE):
                logger.warning("PROFILE_DIALOG: window mentions the application itself -- not clicking anything")
                return False

            # A type-ahead (Location) left with its suggestion list open holds
            # typed text but no chosen value; pick the matching suggestion.
            for i in range(dialog.locator("input[role=combobox][aria-expanded=true], input[aria-autocomplete]").count()):
                box = dialog.locator("input[role=combobox][aria-expanded=true], input[aria-autocomplete]").nth(i)
                typed = (box.input_value() or "").strip()
                if typed and box.get_attribute("aria-expanded") == "true":
                    if not self._click_visible_suggestion(page, box, typed):
                        box.press("ArrowDown")
                        box.press("Enter")
                    page.wait_for_timeout(600)

            blanks = [b for b in self.find_required_blanks(page)["required_still_blank"]]
            dialog_text = dialog.inner_text() or ""
            blanks_here = [b for b in blanks if b and b in dialog_text]
            if blanks_here:
                logger.warning("PROFILE_DIALOG: still blank, not saving yet: %s", blanks_here)
                return False

            save = dialog.get_by_role(
                "button", name=re.compile(r"^\s*(submit|save|continue|create profile|done)\s*$", re.IGNORECASE)
            )
            for i in range(save.count()):
                if save.nth(i).is_visible() and self._click_resiliently(save.nth(i), timeout_ms=5_000):
                    page.wait_for_timeout(4_000)
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=15_000)
                    except Exception:
                        pass
                    logger.info("PROFILE_CREATED: saved the site's candidate profile window")
                    return True
            logger.warning("PROFILE_DIALOG: no Submit/Save button found in the profile window")
        except Exception as exc:
            logger.warning("Profile window handling failed: %s", exc)
        return False

    # ------------------------------------------------------------------
    # Standard questions answered from the profile
    # ------------------------------------------------------------------
    @staticmethod
    def _school_type_candidates(profile) -> list[str]:
        """University/College only when the profile's schools say so."""
        names = " ".join(s for _level, _field, s, _year in (getattr(profile, "education", ()) or ())).lower()
        if "university" in names or "college" in names:
            return ["University/College", "College/University", "University", "College"]
        return []

    def _job_source_name(self) -> str:
        """Where the posting was found, from its link (?utm_source=LinkedIn),
        for "How did you hear about this position?"."""
        if getattr(self, "_job_source", None) is None:
            self._job_source = ""
            try:
                employer = (getattr(self, "employer", "") or "").lower()
                for job_file in Path("data").glob("_job_*.json"):
                    info = json.loads(job_file.read_text(encoding="utf-8-sig"))
                    if employer and info.get("company", "").lower() == employer:
                        if "linkedin" in (info.get("url", "") or "").lower():
                            self._job_source = "LinkedIn"
                        break
            except Exception:
                pass
        return self._job_source

    @staticmethod
    def _with_latest_answers(profile):
        """The profile as it is on disk, when the run's copy predates it.

        A run builds its profile once at start-up. Answers added to config.py
        afterwards -- the most recent employer, say -- were missing from that
        object, so every rule built from them came out empty and five required
        questions stayed blank with nothing logged.
        """
        try:
            if all(hasattr(profile, name) for name in
                   ("current_employer", "current_position_title", "preferred_contact_method")):
                return profile
            import importlib

            import config as config_module
            importlib.reload(config_module)
            fresh = config_module.get_user_profile()
            logger.info("Read the profile again: this run started before its newest answers")
            return fresh
        except Exception as exc:
            logger.debug("Could not re-read the profile: %s", exc)
            return profile

    def note_page_changed(self) -> None:
        """The agent has just written something, so a page that would not move
        on may move now.

        Next failing once marked the page as the last step for good: the
        questionnaire was filled a moment later and the run never tried again,
        reporting a form it was stuck on as ready to submit.
        """
        if getattr(self, "_stuck_on", None):
            logger.info("Something was filled in since Next last failed; it is worth another try")
        self._stuck_on = None

    def _standard_answer_rules(self, profile) -> list[tuple[re.Pattern, list[str]]]:
        """Questions most employers ask in some wording, mapped to the answers
        the user put in their profile. First match wins, so the narrower
        wording ('... for any employer') sits above the general one. Only
        ever fills an empty control -- an answer already there is left alone."""
        profile = self._with_latest_answers(profile)

        def g(name: str, default: str = "") -> str:
            return str(getattr(profile, name, default) or default)

        sponsorship = bool(getattr(profile, "requires_visa_sponsorship", False))
        country = g("country", "United States")
        rules = [
            # On a work visa you ARE authorized, but only for the sponsoring
            # employer, so "for any employer" is No while plain "authorized" is Yes.
            (r"authori[sz]ed to work.*any employer", [g("authorized_for_any_employer")]),
            (r"(authori[sz]ed|eligible) to work", [g("legally_eligible_to_work", "Yes")]),
            (r"sponsor", ["Yes" if sponsorship else "No"]),
            (r"at least 18|18 years of age|over (the age of )?18", [g("at_least_18")]),
            (r"full legal name", [g("full_name")]),
            # "Do you have 3+ years of experience?" is a Yes/No question the
            # profile answers: six years is Yes to three, No to ten.
            (r"(?:at least |minimum of |more than )?(\d+)\s*\+?\s*(?:or more )?years? (?:of )?(?:\w+ ){0,3}experience\?",
             self._years_yes_no),
            # "Address *" on its own means the street address. Email Address
            # and Address Line 2 must not match it.
            (r"^\s*\*?\s*(street |home |mailing )?address(\s*(line\s*)?1)?\s*\*?\s*$",
             [g("address_line1")]),
            # A questionnaire's employment block. The narrow wordings come
            # first: a rule for the employer's name matched every one of these
            # questions and wrote "Capital One" into all five.
            (r"type of business|industry|nature of business", [g("current_employer_type")]),
            (r"dates? of employment|employment dates|period of employment|from\s*/\s*to",
             [g("current_employment_dates")]),
            (r"position title|job title|title held|your title|position held",
             [g("current_position_title")]),
            (r"reason for leaving|why (did|are) you leav", [g("reason_for_leaving")]),
            (r"employer (address|location)|company location", [g("current_employer_location")]),
            (r"(employer|company)(\s*name)?\s*$|company name", [g("current_employer")]),
            (r"preferred contact( method)?|how (would you like|do you prefer) (us )?to (contact|reach)",
             [g("preferred_contact_method", "Email"), "Email", "E-mail", "Email Address"]),
            (r"preferred contact( method)?|how (would you like|do you prefer) (us )?to (contact|reach)",
             [g("preferred_contact_method", "Email"), "Email", "E-mail", "Email Address"]),
            # Asked outright on export-control sections. The profile states it;
            # the agent never works it out from a name or a visa status.
            (r"(country|countries)(/region)?.{0,20}citizenship|citizenship.{0,20}(country|countries)",
             [g("country_of_citizenship")] if g("country_of_citizenship") else []),
            (r"u\.?s\.? citizen|united states citizen|citizen of the (u\.?s\.?|united states)",
             [g("us_citizen"), "No, I am not a U.S. Citizen"] if g("us_citizen").lower() == "no"
             else [g("us_citizen")]),
            (r"clearance", ["I do not have a clearance", "No clearance", "None",
                            "Not applicable", "N/A"]
             if g("security_clearance_level").lower() in {"none", "", "no clearance"}
             else [g("security_clearance_level")]),
            (r"years of (relevant |related |professional )?experience|how many years",
             [str(getattr(profile, "years_experience", "") or ""),
              f"{getattr(profile, 'years_experience', '')} years"]),
            (r"country code|dial\w*\s*code|phone country|country dial",
             [f"{g('phone_country_code', '+1')} {country} of America",
              f"{country} of America ({g('phone_country_code', '+1')})",
              f"{country} ({g('phone_country_code', '+1')})",
              f"({g('phone_country_code', '+1')}) {country}",
              f"{country} of America", country, g('phone_country_code', '+1')]),
            (r"^\s*country(/region)?( of residence)?\s*:?\s*\*?\s*$", [country, "United States of America"]),
            (r"^\s*\*?\s*(state|province)(/province)?(/region)?\s*:?\s*\*?\s*$", [g("state"), "VA", "Virginia (VA)"]),
            (r"veteran", [g("veteran_status"), "I am not a protected veteran", "not a protected veteran"]),
            (r"hispanic or latino", ["Not Hispanic/Latino", "Not Hispanic or Latino", "Not Hispanic",
                                     g("hispanic_or_latino", "No")]
                                     if g("hispanic_or_latino").lower() in {"no", "not hispanic or latino"} else []),
            (r"non-?compete", [g("bound_by_non_compete")]),
            (r"(been|previously|ever been) employed (with|by)|worked for .{0,40}before",
             [g("previously_employed_here")] if g("outside_business_interests_with_competitors") == "No" else []),
            (r"was (the )?degree (achieved|obtained|completed)|degree (achieved|completed)\?", ["Yes"]),
            # Supported by the school's own name in the profile, nothing more.
            (r"school type|type of (school|institution)", self._school_type_candidates(profile)),
            # Only where the posting link says so, or the profile states it.
            (r"how did you hear|how were you referred|source of (your )?application|referral source",
             [self._job_source_name(), g("how_did_you_hear"), "Company Website",
              "Careers Website", "Corporate Website", "Employer Website", "Company Site"]),
            (r"preferred language", [g("preferred_language")]),
            # Employment status: multi-select checkboxes or radio buttons
            (r"employment status|type of (employment|position|work)|work arrangement|employment (type|arrangement)|desired (employment|position) type|interested in",
             list(getattr(profile, "employment_statuses", ("Full-Time",))) if hasattr(profile, "employment_statuses") else []),
            # Only ever matches a disability list: the candidates are disability answers.
            (r"please select one of the options below", [g("disability_status"), "No, I do not have a disability"]),
            (r"disabilit", [g("disability_status"), "No, I do not have a disability"]),
            (r"\brace\b|ethnicit", [g("ethnicity")]),
            (r"^\s*(gender|sex)\b", [g("gender")]),
        ]
        return [(re.compile(p, re.IGNORECASE), cands if callable(cands) else [c for c in cands if c])
                for p, cands in rules]

    @staticmethod
    def _best_option(options: list[str], candidates: list[str]) -> Optional[int]:
        """Index of the option that best matches the candidates: exact text,
        then an option starting with the candidate ('No, I do not have a
        disability and have not had one...'), then containing it. Short
        candidates ('Yes', 'Male') only match exactly -- 'male' is inside
        'female'."""
        def norm(s: str) -> str:
            s = re.sub(r"[^\w\s()+,'-]", " ", s)  # drops flag emoji etc.
            return re.sub(r"\s+", " ", s).strip().lower()

        placeholder = re.compile(r"^\s*(-+\s*)?(no selection|select( one| an option)?|please select|choose( one)?|none selected)(\s*-+)?\s*\.*$")
        opts = ["" if placeholder.match(norm(o)) else norm(o) for o in options]
        for cand in candidates:
            c = norm(cand)
            if not c:
                continue
            for i, o in enumerate(opts):
                if o and o == c:
                    return i
            if len(c) < 6:
                hits = [i for i, o in enumerate(opts) if o and re.match(re.escape(c) + r"(\s|\(|,|$)", o)]
                if len(hits) == 1:
                    return hits[0]
                continue
            # Among partial matches take the SHORTEST option: '(+1) United
            # States' must pick '... United States of America', not the
            # earlier-listed '... United States Minor Outlying Islands'.
            hits = [i for i, o in enumerate(opts) if o and o.startswith(c)]
            if hits:
                return min(hits, key=lambda i: len(opts[i]))
            # Contained somewhere inside: only when it is inside exactly one
            # option. Casey's race list repeats "Not Hispanic or Latino" under
            # every race, and taking the shortest of them answered "Two or
            # More Races" for a candidate whose profile says Asian.
            hits = [i for i, o in enumerate(opts) if o and c in o]
            if len(hits) == 1:
                return hits[0]
        return None

    def _adapter_hook(self, page: Page, name: str, default, *args):
        """Calls a hook on the page's adapter, tolerating its absence.

        Hot-reloading brings in new code while the run holds adapter objects
        built from the old; a hook that isn't there yet must cost the feature,
        never the application. One AttributeError here closed a browser with a
        part-filled Amazon application in it.
        """
        hook = getattr(self.adapter(page), name, None)
        if hook is None:
            logger.debug("Adapter has no %s yet", name)
            return default
        try:
            return hook(*args)
        except Exception as exc:
            logger.warning("Adapter %s failed: %s", name, str(exc).splitlines()[0][:120])
            return default

    @staticmethod
    def _has_answer(value) -> bool:
        """True when a control holds a real answer rather than a placeholder."""
        text = (value or "").strip()
        return bool(text) and not re.match(
            r"^(select an option|select|select one|please select|choose one|-+)$", text, re.I)

    def _years_yes_no(self, question: str) -> list[str]:
        """Yes or No to "N+ years of experience?", from the profile's years."""
        match = re.search(r"(\d+)\s*\+?\s*(?:or more )?years?", question or "", re.IGNORECASE)
        profile = self._with_latest_answers(getattr(self, "_profile", None))
        have = int(getattr(profile, "years_experience", 0) or 0)
        if not match or not have:
            return []
        return ["Yes"] if have >= int(match.group(1)) else ["No"]

    def _rule_for(self, question: str, rules) -> Optional[list[str]]:
        q = (question or "").strip()
        for pattern, candidates in rules:
            if q and pattern.search(q) and candidates:
                # A rule may work its answer out from the question itself.
                if callable(candidates):
                    candidates = candidates(q)
                    if not candidates:
                        continue
                return candidates
        return None

    def answer_standard_questions(self, page: Page, profile) -> None:
        """Answers dropdowns, type-ahead comboboxes and radio groups for the
        standard questions (work authorization, sponsorship, 18+, country,
        EEO self-identification) straight from the profile, then repairs a
        phone number the form rejected."""
        self.accept_consent_dialog(page)
        if not all(hasattr(profile, f) for f in ("education", "us_citizen", "security_clearance_level")):
            # A run that started before new profile fields were added: re-read
            # config.py so a hot reload can use them without restarting.
            try:
                import importlib
                import config as config_module
                profile = importlib.reload(config_module).get_user_profile()
                self._profile = profile
            except Exception as exc:
                logger.warning("Could not re-read the profile: %s", exc)
        rules = self._standard_answer_rules(profile)
        try:
            controls = page.evaluate(
                """() => {
                    const textOf = e => {
                        const ids = (e.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean);
                        const byIds = ids.map(i => document.getElementById(i)?.innerText || '').join(' ').trim();
                        if (byIds) return byIds;
                        if (e.getAttribute('aria-label')) return e.getAttribute('aria-label');
                        if (e.id) {
                            const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`);
                            if (l) return l.innerText;
                        }
                        return '';
                    };
                    const visible = e => !!(e.offsetParent || e.getClientRects().length);
                    // BambooHR builds its State and Country pickers as menu
                    // buttons over a hidden select of zero height, so reading
                    // the select gave one empty option and nothing could be
                    // chosen. The button is the control; its aria-label
                    // carries both the question and the current answer
                    // ("Country United States", "State \u2039Select\u203a").
                    const menuButtons = [...document.querySelectorAll(
                        'button[aria-haspopup][aria-expanded]')].filter(visible);
                    return [...document.querySelectorAll("select, input[role=combobox]"),
                            ...menuButtons]
                        // A control of zero height, or marked aria-hidden, is
                        // the machinery behind a widget rather than the widget:
                        // BambooHR keeps a hidden select behind each menu
                        // button, and answering that one could never work.
                        .filter(e => (e.id || e.getAttribute('data-menu-id')) && visible(e) && !e.disabled
                                     && e.getAttribute('aria-hidden') !== 'true'
                                     && e.getBoundingClientRect().height > 1)
                        .map(e => {
                            // A select is answered when its chosen option is a real
                            // one. Not "past the first option": Schwab's (iCIMS)
                            // Country list holds only the chosen "United States",
                            // so it read as blank and was chosen again every pass.
                            const chosen = e.tagName === 'SELECT' ? e.options[e.selectedIndex] : null;
                            // The first option counts only when the site chose it
                            // (or it is the only one): a browser's default is not
                            // an answer.
                            let value = (e.tagName === 'SELECT'
                                ? (chosen && (chosen.value || '').trim()
                                   && (e.selectedIndex > 0 || chosen.defaultSelected || e.options.length === 1)
                                   && !/^[\\s\\-–—]*(no selection|select( one| an option)?|please select|choose( one)?|make a selection|none selected)?[\\s\\-–—.]*$/i.test(chosen.text)
                                   ? chosen.text : '')
                                : e.value || '').trim();
                            if (!value && e.tagName !== 'SELECT') {
                                // A picker shows its choice beside the input,
                                // whose own value stays empty: every pass read
                                // Country and State as blank and chose them
                                // again, so a run never finished a page.
                                let n = e.parentElement;
                                for (let i = 0; i < 4 && n && !value; i++, n = n.parentElement) {
                                    if (n.querySelectorAll('input:not([type=hidden]), select, textarea').length > 1) break;
                                    const shown = n.querySelector('[class*=singleValue], [class*=single-value],'
                                                                + '[class*=selection-item]:not([class*=search])');
                                    if (shown) value = (shown.innerText || '').trim();
                                }
                            }
                            // SuccessFactors pickers show their placeholder as the value.
                            if (/^(-+\\s*)?(no selection|select|please select|choose one)(\\s*-+)?$/i.test(value)) value = '';
                            if (e.tagName === 'BUTTON') {
                                // The question is the field's own label --
                                // "State *" -- while aria-label repeats the
                                // answer with it ("Country United States").
                                // What the button shows is the answer:
                                // "United States", or a placeholder such as
                                // "\u2013Select\u2013" when there is none.
                                const shown = (e.innerText || '')
                                    .replace(/[\u2013\u2014\u2039\u203a<>]/g, ' ').trim();
                                let label = '', n = e.parentElement;
                                for (let i = 0; i < 5 && n && !label; i++, n = n.parentElement) {
                                    const l = n.querySelector('label');
                                    if (l && !l.contains(e)) label = (l.innerText || '').trim();
                                }
                                if (!label) label = (e.getAttribute('aria-label') || '').trim();
                                const empty = !shown || /^(select|please select|choose( one)?|none)$/i.test(shown);
                                return {id: e.id || e.getAttribute('data-menu-id'),
                                        kind: 'menu', question: label,
                                        value: empty ? '' : shown, listbox: ''};
                            }
                            return {id: e.id, kind: e.tagName === 'SELECT' ? 'select' : 'combobox',
                                    question: textOf(e).trim(), value,
                                    listbox: e.getAttribute('aria-controls') || e.getAttribute('aria-owns') || ''};
                        });
                }"""
            )
        except Exception as exc:
            logger.warning("Standard-question scan failed: %s", exc)
            controls = []

        for c in controls:
            if c["value"] and re.search(r"degree obtained|field of study", c["question"] or "", re.IGNORECASE):
                wanted = self._education_answer(page, c, profile)
                if wanted and self._best_option([c["value"]], wanted[:3]) is None:
                    logger.info("Correcting %r: %r belongs to a different degree", c["question"][:40], c["value"])
                    try:
                        page.locator(f"[id={json.dumps(c['id'])}]").fill("")
                    except Exception:
                        pass
                    c["value"] = ""
            if c["value"]:
                continue
            candidates = (self._rule_for(c["question"], rules) or self._education_answer(page, c, profile)
                          or self._experience_answer(page, c))
            if not candidates:
                continue
            try:
                if c["kind"] == "select":
                    self._answer_select_from_profile(page, c, candidates)
                else:
                    self._answer_combobox_from_profile(page, c, candidates)
            except Exception as exc:
                logger.warning("Could not answer %r: %s", c["question"][:60], exc)

        # The same rules, applied to questions only the adapter can see.
        adapter = self.adapter(page)
        for question in self._adapter_hook(page, "platform_questions", [], page):
            selector = f"[data-questionid={json.dumps(question.get('qid', ''))}]"
            if self._has_answer(question.get("value")):
                wanted = self._rule_for(question.get("question", ""), rules)
                ours = self.values.is_ours(page, selector, question.get("value", ""))
                # Correct an answer the agent itself got wrong; an answer that
                # came from the user or the site is left exactly as it is.
                if not (wanted and ours and self._best_option([question["value"]], wanted) is None):
                    continue
                logger.warning("CORRECTING %r: %r is not what the profile says",
                               question["question"][:50], question["value"][:40])
            candidates = self._rule_for(question.get("question", ""), rules)
            if not candidates:
                continue
            options = question.get("options") or []
            answer = candidates[0]
            if options:
                index = self._best_option(options, candidates)
                if index is None:
                    self.note_ambiguous_choice(question["question"], options, candidates[0])
                    continue
                answer = options[index]
            if self._adapter_hook(page, "answer_platform_question", False,
                                  self, page, question.get("qid", ""), answer):
                logger.info("PROFILE_ANSWER: %r -> %r", question["question"][:60], answer[:40])

        # Answers can reveal new required fields (choosing Country adds State),
        # so scan once more for anything that has just appeared.
        if controls and not getattr(self, "_standard_second_pass", False):
            self._standard_second_pass = True
            try:
                page.wait_for_timeout(600)
                self.answer_standard_questions(page, profile)
            finally:
                self._standard_second_pass = False
            return

        self._answer_radio_groups_from_profile(page, rules)
        self._answer_checkbox_groups_from_profile(page, rules)
        self._answer_text_questions(page, profile)
        self._repair_rejected_phone(page, profile)

    @staticmethod
    def _entry_text(page: Page, control_id: str, marker: str) -> str:
        """Text and input values of the ONE repeated entry (a job, a degree)
        this control sits in: the largest ancestor that still contains a single
        `marker` label. Climbing further takes in the neighbouring entries --
        which is how a Bachelor's entry got the Master's answers."""
        try:
            return (page.locator(f"[id={json.dumps(control_id)}]").evaluate("""(e, marker) => {
                const re = new RegExp(marker, 'i');
                const count = n => [...n.querySelectorAll('label')].filter(l => re.test(l.innerText || '')).length;
                let n = e, best = '';
                for (let i = 0; i < 12 && n.parentElement; i++) {
                    n = n.parentElement;
                    const c = count(n);
                    if (c > 1) break;
                    if (c === 1) best = (n.innerText || '') + ' ' + [...n.querySelectorAll('input')].map(x => x.value).join(' ');
                }
                return best;
            }""", marker) or "").lower()
        except Exception:
            return ""

    def _experience_answer(self, page: Page, control: dict) -> Optional[list[str]]:
        """Per-job questions in a work-experience entry: the entry's position
        by start date (1 = most recent) and number of people managed (the
        lowest option -- the profile records no direct reports)."""
        q = control["question"] or ""
        if re.search(r"order experience|most recent 1", q, re.IGNORECASE):
            try:
                starts = page.evaluate("""id => {
                    const dateRe = new RegExp('(\\\\d{2})/(\\\\d{2})/(\\\\d{4})');
                    const read = el => { const m = (el.innerText || '').match(dateRe)
                        || [...el.querySelectorAll('input')].map(x => x.value).join(' ').match(dateRe);
                        return m ? m[3] + m[1] + m[2] : ''; };
                    const entries = [...document.querySelectorAll('label')].filter(l => /order experience/i.test(l.innerText || ''))
                        .map(l => { let n = l; for (let i = 0; i < 12 && n.parentElement; i++) { n = n.parentElement;
                            if ([...n.querySelectorAll('label')].filter(x => /order experience/i.test(x.innerText || '')).length > 1) return null;
                            if ([...n.querySelectorAll('label')].some(x => /start date/i.test(x.innerText || ''))) return n; } return null; });
                    const mine = document.getElementById(id);
                    return entries.map(n => n ? {start: read(n), mine: n.contains(mine)} : null).filter(Boolean);
                }""", control["id"])
                me = next((s for s in starts if s["mine"]), None)
                if me and me["start"] and all(x["start"] for x in starts):
                    rank = 1 + sum(1 for x in starts if x["start"] > me["start"])
                    return [str(rank)]
                # Dates unreadable: fall back to the entries' order on the page.
                position = page.evaluate("""id => [...document.querySelectorAll('input[role=combobox]')]
                    .filter(e => /order experience/i.test(e.getAttribute('aria-label') || ''))
                    .findIndex(e => e.id === id)""", control["id"])
                if position is not None and position >= 0:
                    return [str(position + 1)]
            except Exception:
                pass
            return None
        if re.search(r"number of people managed|direct reports|people (you )?manage", q, re.IGNORECASE):
            managed = str(getattr(getattr(self, "_profile", None), "people_managed", "") or "")
            return [managed] if managed else None  # unknown: the user answers it
        return None

    def _education_answer(self, page: Page, control: dict, profile) -> Optional[list[str]]:
        """Degree / field-of-study pickers inside an education entry: works out
        which of the profile's degrees the entry is (by the school or field
        text around it) and answers for that degree."""
        q = control["question"] or ""
        wants_degree = re.search(r"degree (obtained|earned|level|type)|^\W*degree\W*$|highest degree", q, re.IGNORECASE)
        wants_field = re.search(r"field of study|major|area of study|discipline", q, re.IGNORECASE)
        degrees = getattr(profile, "education", ()) or ()
        if not (wants_degree or wants_field) or not degrees:
            return None
        try:
            entry_text = self._entry_text(page, control["id"], r"school name")
        except Exception:
            entry_text = ""
        chosen = None
        for level, field, school, _year in degrees:
            keys = [w for w in re.split(r"\W+", school.lower()) if len(w) > 3] + [field.lower()]
            if any(k in entry_text for k in keys):
                chosen = (level, field)
                break
        if chosen is None:
            if len(degrees) == 1 or "bachelor" not in entry_text and "master" not in entry_text:
                return None  # can't tell which degree this entry is -- leave it
            chosen = next(((l, f) for l, f, _s, _y in degrees if l.lower()[:4] in entry_text), None)
            if chosen is None:
                return None
        level, field = chosen
        if wants_degree:
            base = "Master" if level.lower().startswith("master") else "Bachelor"
            return [f"{base}'s Degree", f"{base}s Degree", f"{base}'s", f"{base}s", base]
        # The real field of study, or the list's own "not available" choice --
        # never a different subject that happens to be on the list.
        return [field, " ".join(field.split()[:2]), "Other"]

    def _answer_select_from_profile(self, page: Page, control: dict, candidates: list[str]) -> None:
        select = page.locator(f"[id={json.dumps(control['id'])}]")
        options = select.locator("option").all_inner_texts()
        idx = self._best_option(options, candidates)
        if idx is None:
            self.note_ambiguous_choice(control["question"], options, candidates[0] if candidates else "")
            logger.info("PROFILE_ANSWER: no option for %r among %s", control["question"][:60], options[:6])
            return
        select.select_option(label=options[idx], timeout=5_000)
        self.values.record(page, f"[id={json.dumps(control['id'])}]", options[idx], "profile:standard answer")
        logger.info("PROFILE_ANSWER: %r -> %r", control["question"][:60], options[idx])

    def _salary_band(self, texts: list[str]) -> int | None:
        """Which offered pay band to pick when a form asks for a salary range
        rather than a number.

        Employers word bands their own way ("$120k - $160k", "150,000-175,000"),
        so the band is chosen by overlap with the range in the profile. A band
        that doesn't overlap it at all is never picked -- that would be asking
        for money the user didn't ask for.
        """
        profile = getattr(self, "_profile", None)
        low, high = getattr(profile, "salary_min", 0), getattr(profile, "salary_max", 0)
        if not (low and high):
            return None

        def amounts(text: str) -> list[int]:
            found = []
            for number, k in re.findall(r"([\d][\d,]*)\s*(k?)", text, re.I):
                try:
                    value = float(number.replace(",", ""))
                except ValueError:
                    continue
                if k:
                    value *= 1_000
                if value >= 1_000:
                    found.append(int(value))
            return found

        best, best_overlap = None, 0
        for index, text in enumerate(texts):
            values = amounts(text)
            if not values:
                continue
            band_low, band_high = min(values), max(values)
            if band_high == band_low:  # an open-ended band ("$150,000+")
                band_high = band_low * 2
            overlap = min(high, band_high) - max(low, band_low)
            if overlap > best_overlap:
                best, best_overlap = index, overlap
        if best is not None:
            logger.info("PROFILE_ANSWER: salary band %r covers %s-%s", texts[best], low, high)
        return best

    def displayed_value(self, field) -> str:
        """What a picker shows as chosen.

        A react-select style combobox (Greenhouse) clears its search input
        once you pick an option and renders the choice in a sibling element,
        so input_value() alone reports an answered field as still blank.
        """
        try:
            value = field.input_value()
        except Exception:
            value = ""
        if value:
            return value
        try:
            return field.evaluate("""e => {
                const shownIn = n => n && n.querySelector(
                    '[class*=singleValue], [class*=single-value], [class*=multiValue], [class*=multi-value],'
                    + '[class*=selection-item]:not([class*=search])');
                let n = e.parentElement;
                for (let i = 0; i < 4 && n; i++, n = n.parentElement) {
                    // Another field inside this ancestor means we have left
                    // the control; its value is not ours to report.
                    if (n.querySelectorAll('input:not([type=hidden]), select, textarea').length > 1) break;
                    const shown = shownIn(n);
                    if (shown) return shown.innerText.trim();
                }
                return '';
            }""") or ""
        except Exception:
            return ""

    def visible_input_count(self, page: Page) -> int:
        try:
            return page.locator(
                "input:not([type=hidden]):not([type=search]):visible, select:visible, textarea:visible").count()
        except Exception:
            return 0

    _JOB_DESCRIPTION_WORDS = re.compile(
        r"responsibilities|qualifications|requirements|job description|about the (role|job|position)|"
        r"what you('ll| will) do|who you are|job (id|number|requisition)|posted",
        re.IGNORECASE)

    def on_job_description(self, page: Page) -> bool:
        """True when the page is a job posting rather than the application.

        A posting has an Apply control, reads like a job description and has
        no form of its own. Schwab's run was resumed at its posting's address
        and stopped there, taking the posting for the form.
        """
        # Some sites label the final Submit "Apply": once the agent has filled
        # anything on the application, no page counts as the posting again.
        if getattr(self, "_form_filled_this_run", False):
            return False
        if self.visible_input_count(page) > 3 or self.is_review_step(page):
            return False
        try:
            body = page.inner_text("body", timeout=5_000)
        except Exception:
            return False
        if not self._JOB_DESCRIPTION_WORDS.search(body) or re.search(
                r"review (your|and submit)|submit (your|this) application", body, re.IGNORECASE):
            return False
        control = self.find_apply_control(page)
        if control is not None:
            try:
                if (control.get_attribute("type") or "").lower() == "submit":
                    return False
            except Exception:
                pass
            return True
        return bool(self.apply_destination(page))

    def open_embedded_form(self, page: Page):
        """The page to work on when a site shows its job inside a frame.

        Returns the page itself when its own document is the form (or when no
        frame holds anything to apply with). A frame from an application system
        on another site is opened as a page of its own. A frame from the site
        itself -- iCIMS, which sends a frame opened on its own straight back --
        is worked inside, through FramedPage.
        """
        # Not isinstance: reloading the code mid-run makes a new FramedPage class.
        if getattr(page, "top", None) is not None:
            if page.frame() != page.top.main_frame:
                return page
            page = page.top  # the site has left its frame
        try:
            if self.visible_input_count(page) > 3:
                return page  # the outer page is the form

            def site(url: str) -> str:
                return ".".join((urlparse(url).hostname or "").split(".")[-2:])

            best, best_count = None, 0
            for frame in page.frames[1:]:
                url = frame.url or ""
                if not url.startswith("http") or re.search(
                        r"recaptcha|hcaptcha|captcha|google\.com/maps|youtube|doubleclick|onetrust|cookielaw|"
                        r"googletagmanager|facebook|linkedin|twitter|chat|paradox|olivia|drift|intercom|"
                        r"newsletter|talent-?community", url, re.IGNORECASE):
                    continue
                # Only the employer's own site or an application system: a
                # frame from anywhere else is an advert or a widget.
                if site(url) != site(page.url) and not re.search(
                        r"icims|myworkday|workday|taleo|greenhouse|lever\.co|ashby|successfactors|jobvite|"
                        r"smartrecruiters|brassring|ultipro|ukg|dayforce|adp\.com|oraclecloud|eightfold|"
                        r"phenom|avature|bamboohr|recruitee|workable|applytojob", url, re.IGNORECASE):
                    continue
                try:
                    count = frame.locator("input:not([type=hidden]), select, textarea, "
                                          "a:has-text('Apply'), button:has-text('Apply')").count()
                except Exception:
                    continue
                if count > best_count:
                    best, best_count = frame, count
            if best is None:
                return page
            if site(best.url) == site(page.url):
                logger.info("The job is shown inside a frame of the site's own; working inside it")
                self.note_page_changed()
                return FramedPage(page, best)
            logger.info("The job is shown inside a frame; opening the frame on its own: %s", best.url[:90])
            page.goto(best.url, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                page.wait_for_timeout(2_000)
            self.note_page_changed()
        except Exception as exc:
            logger.debug("Frame check failed: %s", str(exc).splitlines()[0][:100])
        return page

    # Never clicked on Claude's say-so, whatever it reads on the screen. The
    # Apply that opens a form from a posting is allowed separately below.
    _NEVER_CLICK = re.compile(
        r"\bsubmit|send (my |your |the |this )?application|\bfinish\b|certify|attest|\bsignature\b|"
        r"\be-?sign\b|sign (here|below)|agree to the terms|linked ?in|\bindeed\b|facebook|"
        r"log ?out|sign ?out|withdraw|delete|remove",
        re.IGNORECASE)

    def safe_to_click_for_claude(self, page: Page, label: str) -> bool:
        label = " ".join((label or "").split())
        if not label or self._NEVER_CLICK.search(label) or safety.is_attestation(label):
            return False
        if safety.is_submit_label(label):
            # "Apply" sends the application on some final pages; it only opens
            # the form from a posting that has no form of its own.
            return bool(re.match(r"^\s*apply\b", label, re.IGNORECASE)) and self.on_job_description(page)
        return True

    def look_and_act(self, page: Page, claude, goal: str) -> tuple[str, bool]:
        """Looks at the page the way a person would and takes the next step.

        Used when the usual reading finds nothing to fill and nothing to press.
        Claude names the control; the agent finds it on the page and clicks it
        -- unless it submits, signs, certifies, deletes or uses a LinkedIn,
        Indeed or Facebook sign-in, which is refused here whatever Claude says.
        Returns (what kind of page it is, whether something was clicked).

        A CAPTCHA ends it: nothing is clicked while one is showing, and nothing
        inside a CAPTCHA's frame is ever clicked. On Schwab's sign-in the
        screenshot was read as a sign-in prompt and the puzzle's own "Skip"
        was clicked before the usual CAPTCHA check had run.
        """
        if safety.captcha_visible(page):
            logger.info("LOOKED: a CAPTCHA is showing -- only you can complete it; the agent does nothing")
            return "captcha", False
        try:
            shot = page.screenshot(full_page=False, timeout=15_000)
            seen = claude.read_page(shot, page.url, goal)
        except Exception as exc:
            logger.warning("LOOKED: could not read the page (%s)", str(exc).splitlines()[0][:100])
            return "", False
        kind, label, why = seen.get("page", ""), seen.get("click", ""), seen.get("why", "")
        logger.info("LOOKED: %s -- %s%s", kind.replace("_", " ") or "a page", why[:140],
                    f" -> click {label[:40]!r}" if label else "")
        if kind == "captcha" or safety.captcha_visible(page):
            logger.info("LOOKED: a CAPTCHA is showing -- only you can complete it; the agent does nothing")
            return "captcha", False
        if not label:
            return kind, False
        if not self.safe_to_click_for_claude(page, label):
            logger.info("LOOKED: not clicking %r -- the agent never presses that on its own", label[:40])
            return kind, False
        exact = re.compile(rf"^\s*{re.escape(label)}\s*$", re.IGNORECASE)
        loose = re.compile(re.escape(label), re.IGNORECASE)
        scopes = [page] + [f for f in page.frames[1:]
                           if (f.url or "").startswith("http") and not safety.is_captcha_frame(f.url)]
        before = self._page_fingerprint(page)
        for scope in scopes:
            candidates = [scope.get_by_role("button", name=exact), scope.get_by_role("link", name=exact),
                          scope.get_by_role("button", name=loose), scope.get_by_role("link", name=loose),
                          scope.get_by_role("menuitem", name=loose), scope.get_by_role("tab", name=loose),
                          scope.get_by_text(exact), scope.get_by_label(exact)]
            for candidate in candidates:
                try:
                    for i in range(min(candidate.count(), 4)):
                        el = candidate.nth(i)
                        if not el.is_visible():
                            continue
                        on_it = " ".join(((el.inner_text(timeout=1_000) or "") + " " +
                                          (el.get_attribute("aria-label") or "")).split())
                        if on_it and not self.safe_to_click_for_claude(page, on_it):
                            continue
                        if safety.captcha_visible(page):
                            logger.info("LOOKED: a CAPTCHA appeared -- only you can complete it; not clicking")
                            return "captcha", False
                        el.scroll_into_view_if_needed(timeout=3_000)
                        el.click(timeout=5_000)
                        page.wait_for_timeout(2_500)
                        try:
                            page.wait_for_load_state("networkidle", timeout=10_000)
                        except Exception:
                            pass
                        self.note_page_changed()
                        moved = self._page_fingerprint(page) != before
                        logger.info("LOOKED: clicked %r%s", label[:40], "" if moved else " (the page looks the same)")
                        return kind, True
                except Exception as exc:
                    logger.debug("Vision click failed: %s", str(exc).splitlines()[0][:100])
                    continue
        logger.info("LOOKED: could not find %r on the page to click", label[:40])
        return kind, False

    def open_picker_control(self, page: Page, field) -> bool:
        """Clicks whatever opens this picker.

        Ant Design (Dayforce) puts a zero-width type=search input inside the
        control and covers it with the selector div: clicking the input waited
        the full thirty seconds and then gave up, so Country, State and "How
        did you hear" were left blank on every pass. The wrapper is what a
        person clicks, and Ant opens on mousedown rather than click.
        """
        try:
            field.click(timeout=4_000)
            return True
        except Exception:
            pass
        for wrapper in ("xpath=ancestor::*[contains(@class,'ant-select-selector')][1]",
                        "xpath=ancestor::*[contains(@class,'select-selector')][1]",
                        "xpath=ancestor::*[contains(@class,'select')][1]",
                        "xpath=.."):
            try:
                target = field.locator(wrapper).first
                if not target.count():
                    continue
                target.click(timeout=4_000)
                return True
            except Exception:
                continue
        try:
            field.evaluate("""e => {
                const box = e.closest('[class*=select-selector], [class*=select], [role=combobox]') || e.parentElement;
                for (const type of ['mousedown', 'mouseup', 'click']) {
                    box.dispatchEvent(new MouseEvent(type, {bubbles: true}));
                }
                e.focus();
            }""")
            return True
        except Exception as exc:
            logger.warning("Could not open a picker: %s", str(exc).splitlines()[0][:100])
            return False

    def _answer_combobox_from_profile(self, page: Page, control: dict, candidates: list[str]) -> None:
        field = page.locator(f"[id={json.dumps(control['id'])}]")
        if field.count() == 0:
            field = page.locator(f"[data-menu-id={json.dumps(control['id'])}]")
        wants_dial_code = bool(re.search(r"country code|dial|phone|^\s*\*?\s*country",
                                         control.get("question", ""), re.I))
        scope = page.locator(f"[id={json.dumps(control['listbox'])}]") if control["listbox"] else page
        if scope is not page and scope.count() == 0:
            scope = page  # the named list isn't in the page (SuccessFactors renders it elsewhere)

        # A phone field's dialling-code list ("Afghanistan+93") is open on many
        # application pages at once. Reading "any visible option" therefore
        # offered a clearance question the country list; these are the options
        # of the field being answered, not of the page.
        dial_code = re.compile(r"\+\d{1,4}$")

        def visible_options():
            if scope is not page:
                opts = scope.locator("[role=option]")
                texts = [t.strip() for t in opts.all_inner_texts()]
                if not texts:
                    # SuccessFactors paginated pickers list plain <li> items.
                    opts = scope.locator("li")
                    texts = [t.strip() for t in opts.all_inner_texts()]
                if texts:
                    return opts, texts
            # The field owns no named list: prefer a react-select menu
            # (Greenhouse renders it outside the field, with no aria link
            # back), then any open list that isn't the dialling-code one.
            for selector in ("[role=menuitem]:visible, [class*=MenuItem]:visible",
                             "[class*=select__option]:visible",
                             # Ant Design (Dayforce) renders its menu in a
                             # portal at the end of the body, with the options
                             # as divs rather than option elements.
                             ".ant-select-dropdown:not(.ant-select-dropdown-hidden) "
                             "[class*=ant-select-item-option]:visible",
                             "[class*=select-item-option]:visible",
                             "[class*=menu] [role=option]:visible",
                             "[role=option]:visible",
                             "[role=listbox]:visible li:visible"):
                opts = page.locator(selector)
                if not opts.count():
                    continue
                texts = [t.strip() for t in opts.all_inner_texts()]
                if texts and not wants_dial_code and                         sum(bool(dial_code.search(t)) for t in texts) > len(texts) / 2:
                    continue  # this is the phone widget's country list
                if texts:
                    return opts, texts
            return page.locator("[class*=select__option]:visible"), []

        try:
            field.evaluate("e => e.scrollIntoView({block: 'center'})")
        except Exception:
            pass
        self.open_picker_control(page, field)
        page.wait_for_timeout(700)
        if self.adapter(page).open_picker(page, control) and control.get("listbox"):
            scope = page.locator(f"[id={json.dumps(control['listbox'])}]")
        opts, texts = visible_options()
        if not texts:
            # Keyboard instead of pointer: a sticky footer over the last picker
            # on the page (IGT's disability question) swallows the clicks.
            try:
                field.evaluate("e => { e.scrollIntoView({block: 'center'}); e.focus(); }")
                field.press("ArrowDown")
                page.wait_for_timeout(2_500)
                opts, texts = visible_options()
                if not texts:
                    field.press_sequentially(candidates[0][:5], delay=80)
                    page.wait_for_timeout(3_000)
                    opts, texts = visible_options()
            except Exception:
                pass
        idx = self._best_option(texts, candidates)
        if idx is None and re.search(r"salary|compensation|pay range", control.get("question", ""), re.I):
            idx = self._salary_band(texts)
        if idx is None and re.search(r"how did you hear|referral source|source of", control.get("question", ""), re.I):
            # Segra's list offers Indeed, LinkedIn, Glassdoor and referrals --
            # no company website at all. Where the source the profile states is
            # not among the options, "Other" is what it actually was.
            idx = next((i for i, t in enumerate(texts) if t.strip().lower() == "other"), None)
            if idx is not None:
                logger.info("The source in your profile is not offered here; choosing Other")
        if idx is None:
            # Long lists (countries, dialling codes) only render a slice until
            # you type; filter on the candidate's plain words.
            term = re.sub(r"\([^)]*\)", "", candidates[0]).strip()
            self.open_picker_control(page, field)
            try:
                field.fill("", timeout=3_000)
            except Exception:
                pass  # not editable until it is open; typing still filters
            try:
                field.type(term, delay=30, timeout=5_000)
            except Exception:
                page.keyboard.type(term, delay=30)
            page.wait_for_timeout(900)
            opts, texts = visible_options()
            idx = self._best_option(texts, candidates)
            if idx is None and " " in term:
                # Ant Design renders a handful of options and leaves the rest
                # to its filter, so a whole phrase matches nothing where its
                # first word would: "Company Career Site" against a list that
                # offers "Company Website".
                first_word = term.split()[0]
                try:
                    field.fill("", timeout=2_000)
                except Exception:
                    pass
                try:
                    field.type(first_word, delay=30, timeout=5_000)
                except Exception:
                    page.keyboard.type(first_word, delay=30)
                page.wait_for_timeout(900)
                opts, texts = visible_options()
                idx = self._best_option(texts, candidates)
        if idx is None:
            page.keyboard.press("Escape")
            self.note_ambiguous_choice(control["question"], texts, candidates[0] if candidates else "")
            logger.info("PROFILE_ANSWER: no option for %r among %s", control["question"][:60], texts[:6])
            return
        self._click_resiliently(opts.nth(idx), timeout_ms=4_000)
        page.wait_for_timeout(500)
        shown = self.displayed_value(field)
        self.values.record(page, f"[id={json.dumps(control['id'])}]", shown or texts[idx],
                           "profile:standard answer")
        self.note_page_changed()
        # An earlier pass may have filed this question as unanswerable (before
        # the right option list was found); it is answered now.
        self.clear_ambiguous(control["question"])
        logger.info("PROFILE_ANSWER: %r -> %r (now %r)", control["question"][:60], texts[idx], shown)

    def radio_groups(self, page: Page) -> list[dict]:
        """Every radio question on the page: its text, its options, and whether
        one is chosen.

        A group is a role="radiogroup" or fieldset when the page provides one,
        and a shared name attribute otherwise. Google gives all thirteen radios
        on its form the same name and separates the questions by container, so
        grouping by name alone made them one question with thirteen answers.

        Each option gets a stable id, so a choice can be made on exactly the
        option it belongs to.
        """
        try:
            return page.evaluate("""() => {
                const visible = e => !!(e.offsetParent || e.getClientRects().length);
                const labelOf = r => {
                    const byFor = r.id && document.querySelector(`label[for="${CSS.escape(r.id)}"]`);
                    return ((byFor && byFor.innerText) || (r.closest('label') || {}).innerText
                            || r.getAttribute('aria-label') || r.value || '').replace(/\\s+/g, ' ').trim();
                };
                const questionOf = (container, radios, labels) => {
                    if (container) {
                        const by = container.getAttribute('aria-labelledby');
                        const named = by && document.getElementById(by);
                        const legend = container.querySelector('legend');
                        const text = (named && named.innerText) || (legend && legend.innerText)
                                   || container.getAttribute('aria-label') || '';
                        if (text.trim()) return text.replace(/\\s+/g, ' ').trim();
                    }
                    // No container text: climb until something other than the
                    // option labels themselves appears.
                    let n = radios[0].parentElement;
                    while (n && !radios.every(r => n.contains(r))) n = n.parentElement;
                    for (let i = 0; i < 4 && n; i++, n = n.parentElement) {
                        let t = n.innerText || '';
                        for (const l of labels) if (l) t = t.split(l).join(' ');
                        t = t.replace(/\\s+/g, ' ').trim();
                        if (t) return t;
                    }
                    return '';
                };

                const groups = [];
                const claimed = new Set();
                let counter = 0;
                const build = (container, radios) => {
                    radios = radios.filter(visible);
                    if (!radios.length) return;
                    radios.forEach(r => { if (!r.id) r.id = 'agent-radio-' + (counter++); });
                    const labels = radios.map(labelOf);
                    const placeholder = /^\\s*(no selection|select|none selected)\\s*$/i;
                    groups.push({
                        key: 'group-' + groups.length,
                        question: questionOf(container, radios, labels).slice(0, 300),
                        labels,
                        ids: radios.map(r => r.id),
                        checked: radios.some((r, i) => r.checked && !placeholder.test(labels[i])),
                        chosen: radios.findIndex(r => r.checked),
                        required: !!(container && (container.getAttribute('aria-required') === 'true'
                                                   || container.querySelector('[required]'))),
                    });
                    radios.forEach(r => claimed.add(r));
                };

                for (const container of document.querySelectorAll('[role=radiogroup], fieldset')) {
                    build(container, [...container.querySelectorAll('input[type=radio]')]);
                }
                const byName = {};
                for (const r of document.querySelectorAll('input[type=radio]')) {
                    if (claimed.has(r) || !r.name) continue;
                    (byName[r.name] ||= []).push(r);
                }
                for (const radios of Object.values(byName)) build(null, radios);
                return groups;
            }""")
        except Exception as exc:
            logger.warning("Radio-group scan failed: %s", str(exc).splitlines()[0][:120])
            return []

    def answer_radio_group(self, page: Page, group: dict, answer: str) -> bool:
        """Chooses one option of one radio question, by that option's own id.

        Answering one question on Google's form re-renders the others, which
        throws away the ids given to their options: the second and third
        questions then had nothing left to click. So a group that has gone
        stale is looked up again, by its question, before giving up.
        """
        for attempt in (group, None):
            current = attempt if attempt is not None else next(
                (g for g in self.radio_groups(page) if g["question"] == group["question"]), None)
            if current is None:
                continue
            index = self._best_option(current.get("labels") or [], [answer])
            if index is None:
                continue
            element = page.query_selector(f"[id={json.dumps(current['ids'][index])}]")
            if element is not None and self.select_radio(page, element):
                self.note_page_changed()
                return True
        return False

    def _answer_radio_groups_from_profile(self, page: Page, rules) -> None:
        self.close_error_message(page)
        for group in self.radio_groups(page):
            if not group["question"]:
                continue
            if group["checked"]:
                # Leave the user's answer alone -- but put right one the agent
                # chose itself when the profile says otherwise. It had ticked
                # "Two or More Races" on Casey's form for a profile saying Asian.
                chosen = group.get("chosen", -1)
                chosen_id = group["ids"][chosen] if 0 <= chosen < len(group["ids"]) else ""
                if not chosen_id or not self.values.is_ours(
                        page, f"[id={json.dumps(chosen_id)}]", group["labels"][chosen]):
                    continue
            candidates = self._rule_for(group["question"], rules)
            index = self._best_option(group["labels"], candidates) if candidates else None
            # A list of races carries the race question's answer even when the
            # page labels it with the question before it -- Casey's put its race
            # list under "Are you Hispanic or Latino?".
            races = sum(bool(re.match(r"\s*(white|black|asian|native|pacific|two or more|american indian)",
                                      label, re.IGNORECASE)) for label in group["labels"])
            if index is None and races >= 3:
                candidates = self._rule_for("race", rules)
                index = self._best_option(group["labels"], candidates) if candidates else None
            if not candidates:
                continue
            if group["checked"] and index == group.get("chosen"):
                continue  # already the right answer
            if group["checked"] and index is not None:
                logger.warning("CORRECTING %r: %r is not what your profile says",
                               group["question"][:50], group["labels"][group["chosen"]][:40])
            if index is None:
                self.note_ambiguous_choice(group["question"], group["labels"], candidates[0])
                continue
            if self.answer_radio_group(page, group, group["labels"][index]):
                self.values.record(page, f"[id={json.dumps(group['ids'][index])}]",
                                   group["labels"][index], "profile:standard answer")
                logger.info("PROFILE_ANSWER: %r -> %r", group["question"][:60], group["labels"][index][:60])
            else:
                logger.warning("Could not answer %r", group["question"][:60])

    def checkbox_groups(self, page: Page) -> list[dict]:
        """Every "choose all that apply" question: its text, its options, and
        which are ticked.

        A group is the container that labels the checkboxes -- role="group",
        a fieldset, or an element carrying its own aria-label, as Google's
        race/ethnicity question does.
        """
        try:
            return page.evaluate("""() => {
                const visible = e => !!(e.offsetParent || e.getClientRects().length);
                const clean = s => (s || '').replace(/SPACE/g, ' ').trim();
                const countBoxes = e => e.querySelectorAll('input[type=checkbox]').length;
                // Each option on Google's form sits in its own wrapper with an
                // aria-label of its own ("Asian"), so the first labelled
                // ancestor is the option, not the question. The question is the
                // nearest ancestor holding more than one of them.
                const labelOf = box => {
                    const byFor = box.id && document.querySelector(`label[for="${CSS.escape(box.id)}"]`);
                    if (byFor && clean(byFor.innerText)) return clean(byFor.innerText);
                    if (box.getAttribute('aria-label')) return clean(box.getAttribute('aria-label'));
                    let n = box.parentElement;
                    for (let i = 0; i < 4 && n && countBoxes(n) <= 1; i++, n = n.parentElement) {
                        const own = n.getAttribute('aria-label') || (n.tagName === 'LABEL' ? n.innerText : '');
                        if (clean(own)) return clean(own);
                    }
                    return clean(box.value);
                };
                const groupOf = box => {
                    let n = box.parentElement;
                    for (let i = 0; i < 8 && n; i++, n = n.parentElement) {
                        if (countBoxes(n) < 2) continue;
                        if (n.matches('[role=group], fieldset') || n.getAttribute('aria-label')
                            || n.querySelector('legend')) return n;
                    }
                    return null;
                };

                const groups = [];
                const seen = new Set();
                let counter = 0;
                for (const box of document.querySelectorAll('input[type=checkbox]')) {
                    if (!visible(box)) continue;
                    const container = groupOf(box);
                    if (!container || seen.has(container)) continue;
                    seen.add(container);
                    const boxes = [...container.querySelectorAll('input[type=checkbox]')].filter(visible);
                    boxes.forEach(b => { if (!b.id) b.id = 'agent-box-' + (counter++); });
                    const legend = container.querySelector('legend');
                    groups.push({
                        key: 'boxes-' + groups.length,
                        question: clean(container.getAttribute('aria-label') || (legend && legend.innerText)
                                        || container.innerText).slice(0, 200),
                        labels: boxes.map(labelOf),
                        ids: boxes.map(b => b.id),
                        checked: boxes.some(b => b.checked),
                    });
                }
                return groups;
            }""".replace("SPACE", r"\s+"))
        except Exception as exc:
            logger.warning("Checkbox-group scan failed: %s", str(exc).splitlines()[0][:120])
            return []

    def _answer_checkbox_groups_from_profile(self, page: Page, rules) -> None:
        """Ticks the option a profile answer names, and only that one.

        An answer has to come from a profile rule, so a consent or attestation
        box -- which no rule matches -- is never ticked here.
        """
        for group in self.checkbox_groups(page):
            question = group.get("question") or ""
            if group["checked"] or not question or safety.is_attestation(question):
                continue
            candidates = self._rule_for(question, rules)
            if not candidates:
                continue
            index = self._best_option(group["labels"], candidates)
            if index is None:
                self.note_ambiguous_choice(question, group["labels"], candidates[0])
                continue
            selector = f"[id={json.dumps(group['ids'][index])}]"
            element = page.query_selector(selector)
            if element is None:
                continue
            try:
                if not self.select_radio(page, element):  # same label-covers-input problem
                    continue
            except Exception as exc:
                logger.warning("Could not tick %r: %s", question[:50], str(exc).splitlines()[0][:100])
                continue
            self.values.record(page, selector, group["labels"][index], "profile:standard answer")
            logger.info("PROFILE_ANSWER: %r -> %r", question[:60], group["labels"][index][:50])

    def _answer_text_questions(self, page: Page, profile) -> None:
        """Free-text questions with a known answer: salary expectations (the
        profile's range) and a plain "Today's Date". A signature date under a
        legal statement is NOT filled here -- that stays with the user."""
        from datetime import date
        lo, hi = getattr(profile, "salary_min", 0), getattr(profile, "salary_max", 0)
        answers = []
        if lo and hi:
            answers.append((re.compile(r"salary expectation|desired salary|expected salary|compensation expectation", re.I),
                            f"${lo:,} - ${hi:,} per year"))
        answers.append((re.compile(r"^\s*\*?\s*today[’']?s date\s*:?\s*\*?\s*$", re.I), date.today().strftime("%m/%d/%Y")))
        # Employment status as text field
        emp_status = getattr(profile, "employment_statuses", ("Full-Time",))
        if emp_status and isinstance(emp_status, (tuple, list)):
            answers.append((re.compile(r"employment status|type of.*?work|desired.*?type", re.I), str(emp_status[0])))
        try:
            boxes = page.evaluate("""() => [...document.querySelectorAll('input[type=text], input:not([type]), textarea')]
                .filter(e => e.id && e.getClientRects().length && !e.value && !e.readOnly && !e.disabled
                             && e.getAttribute('role') !== 'combobox')
                .map(e => {
                    let q = ((document.querySelector(`label[for="${CSS.escape(e.id)}"]`)?.innerText)
                             || e.getAttribute('aria-label') || '').trim();
                    if (!q) {  // unlinked label: the nearest short text above the box
                        let n = e.parentElement;
                        for (let i = 0; i < 4 && n && !q; i++, n = n.parentElement) {
                            const t = [...n.querySelectorAll('label, span, div')]
                                .find(x => { const s = (x.innerText || '').trim();
                                    return !x.contains(e) && x.children.length <= 2 && s.length < 80
                                        && s.replace(/[*]/g, '').trim().length > 1 && !/is required/i.test(s); });
                            if (t) q = t.innerText.trim();
                        }
                    }
                    return {id: e.id, q, placeholder: e.placeholder || ''};
                })""")
        except Exception:
            return
        today = date.today().strftime("%m/%d/%Y")
        if self.adapter(page).set_date(self, page, r"today[’']?s date", today):
            logger.info("PROFILE_ANSWER: Today's Date -> %r", today)

        # The same rules the pickers use. A questionnaire asks for the most
        # recent employer, its type of business, the dates and the position as
        # plain text boxes; those rules lived only on the picker path, so the
        # fields were detected, matched nothing here, and stayed empty.
        profile_rules = self._standard_answer_rules(profile)
        logger.info("TEXTQ: %d empty text box(es) on this page: %s",
                    len(boxes), [ (b.get('q') or '')[:34] for b in boxes ][:6])
        for box in boxes:
            question = " ".join((box["q"] or "").split())
            if question:
                from_profile = self._rule_for(question, profile_rules)
                if from_profile and from_profile[0]:
                    answers = answers + [(re.compile(re.escape(question), re.I), from_profile[0])]
            for pattern, value in answers:
                if question and pattern.search(question):
                    if self.set_value(page, f"[id={json.dumps(box['id'])}]", value, question,
                                      source="profile:standard answer"):
                        logger.info("PROFILE_ANSWER: %r -> %r", question[:60], value)
                    else:
                        # It used to break in silence here, so a question the
                        # agent had an answer for looked untouched.
                        logger.warning("Could not write %r into %r", value[:40], question[:60])
                    break

    def _repair_rejected_phone(self, page: Page, profile) -> None:
        """Some forms accept only digits, dashes and parentheses -- the space in
        '(571) 354-5212' made one reject it. Re-enter it as 571-354-5212, but
        only where the form has actually flagged the field invalid. Also handles
        phone_mobile, phone_home, and phone_work fields."""
        # Extract base 10 digits from configured phone numbers
        phone_numbers = {
            "phone": getattr(profile, "phone", "") or "",
            "phone_mobile": getattr(profile, "phone_mobile", "") or "",
            "phone_home": getattr(profile, "phone_home", "") or "",
            "phone_work": getattr(profile, "phone_work", "") or "",
        }
        
        # Convert to standard formats for retry
        formats = {}
        for key, phone in phone_numbers.items():
            if phone:
                digits = re.sub(r"\D", "", phone)[-10:]
                if len(digits) == 10:
                    formats[key] = {
                        "dashed": f"{digits[:3]}-{digits[3:6]}-{digits[6:]}",
                        "parens": f"({digits[:3]}) {digits[3:6]}-{digits[6:]}",
                        "plain": digits
                    }
        
        if not formats:
            return
        
        # Find all phone input fields (including those without aria-invalid)
        phones = page.locator(
            "input[type=tel], input[type=text][id*=phone i], input[type=text][name*=phone i], "
            "input[type=text][placeholder*=phone i], input[type=text][id*=mobile i], "
            "input[type=text][id*=home i]"
        )
        
        for i in range(phones.count()):
            field = phones.nth(i)
            try:
                current = (field.input_value() or "").strip()
                if not current:  # Already empty, skip
                    continue
                    
                # Get field label to determine which phone type to use
                label = (field.get_attribute("aria-label") or "").lower()
                label += " " + (field.get_attribute("placeholder") or "").lower()
                label += " " + (field.get_attribute("id") or "").lower()
                
                # Determine which phone type this field is
                phone_key = "phone"  # default
                if "mobile" in label or "cell" in label:
                    phone_key = "phone_mobile"
                elif "home" in label:
                    phone_key = "phone_home"
                elif "work" in label:
                    phone_key = "phone_work"
                
                if phone_key not in formats:
                    continue
                
                # Try different formats until one works
                for format_name in ["dashed", "parens", "plain"]:
                    new_val = formats[phone_key][format_name]
                    if current != new_val:
                        selector = f"[id={json.dumps(field.get_attribute('id') or '')}]" if field.get_attribute("id") else ""
                        if selector and not self.values.may_write(page, selector, current):
                            break  # User's own number, don't touch
                        
                        field.fill(new_val, timeout=4_000)
                        field.press("Tab")
                        page.wait_for_timeout(500)
                        if selector:
                            self.values.record(page, selector, new_val)
                        logger.info("Re-entered %s phone as %s format: %s", phone_key, format_name, new_val)
                        break  # Moved to next field
            except Exception as exc:
                logger.warning("Could not repair phone field: %s", exc)

    def close_error_message(self, page: Page) -> bool:
        """Closes a message box that only reports errors -- "Errors Found:
        Either Self-Identify or Decline to Identify" -- so the fields it
        complains about can be answered. Its text is logged; nothing in it is
        agreed to."""
        try:
            boxes = page.locator("[role=dialog], [role=alertdialog], .dijitDialog, [class*=modal i]")
            for i in range(min(boxes.count(), 6)):
                box = boxes.nth(i)
                if not box.is_visible():
                    continue
                said = " ".join((box.inner_text() or "").split())
                if not re.search(r"\berrors? found\b|\bplease correct\b|\berror\b", said, re.IGNORECASE):
                    continue
                closer = box.locator(
                    "[aria-label*=close i], [title*=close i], .dijitDialogCloseIcon, [class*=close i], "
                    "button:has-text('OK'), button:has-text('Close')").first
                if closer.count():
                    closer.click(timeout=3_000)
                    page.wait_for_timeout(600)
                    logger.info("Closed the form's error message: %r", said[:120])
                    return True
        except Exception as exc:
            logger.debug("Could not close an error message: %s", str(exc).splitlines()[0][:100])
        return False

    def accept_consent_dialog(self, page: Page) -> bool:
        """Accepts an application-form pop-up such as 'Data Privacy Agreement'
        that sits over the form, when the profile allows it
        (accept_application_privacy_prompts). Never touches cookie banners
        or anything that isn't a privacy/consent prompt."""
        profile = getattr(self, "_profile", None)
        if profile is not None and not getattr(profile, "accept_application_privacy_prompts", True):
            return False
        try:
            # By role as well as by attribute: ADP draws its privacy pop-up
            # inside a web component's shadow DOM, where it is on screen but
            # absent from the page source. Role-based lookup reaches it.
            candidates = [page.get_by_role("dialog"), page.get_by_role("alertdialog"),
                          page.locator("[role=dialog], [aria-modal=true]")]
            dialogs_seen = []
            for group in candidates:
                try:
                    dialogs_seen += [group.nth(i) for i in range(group.count())]
                except Exception:
                    continue
            logger.debug("CONSENT_SCAN: %d dialog(s) on the page: %s", len(dialogs_seen),
                        [(d.inner_text(timeout=1_000) or '')[:50].replace(chr(10), ' ')
                         for d in dialogs_seen[:4] if d.is_visible()])
            for dialog in dialogs_seen:
                if not dialog.is_visible():
                    continue
                # A web component's dialog shows its buttons from inside its
                # shadow DOM and its words from outside it: ADP's read as just
                # "Disagree Agree", so it never looked like a privacy prompt.
                # The component's own text, name and heading say what it is.
                text = dialog.evaluate("""e => {
                    const host = e.getRootNode && e.getRootNode().host;
                    const by = (e.getAttribute('aria-labelledby') || '').split(/ +/)
                        .map(id => (document.getElementById(id) || {}).textContent || '').join(' ');
                    return [e.getAttribute('aria-label') || '', by,
                            (host && host.textContent) || '', e.textContent || ''].join(' ');
                }""") or dialog.inner_text() or ""
                logger.debug("CONSENT_TEXT: %r", " ".join(text.split())[:160])
                if not re.search(r"privacy|consent|data protection", text, re.IGNORECASE):
                    continue
                if re.search(r"cookie", text, re.IGNORECASE):
                    continue
                if safety.is_attestation(text):
                    logger.info("LEFT_FOR_YOU: this pop-up asks you to certify something -- not accepting it")
                    continue
                # By role, not tag: ADP's Agree is an <sdf-button> web
                # component, and a search for <button> elements found none.
                agree = re.compile(r"^\s*(i agree|agree|i accept|accept|agree and continue|accept and continue)\s*$",
                                   re.IGNORECASE)
                button = dialog.get_by_role("button", name=agree).first
                if not button.count():
                    button = dialog.locator("button, [role=button], sdf-button").filter(has_text=agree).first
                if not button.count():
                    continue
                # Pressed until the pop-up is actually gone. ADP's Agree took
                # a pointer click without closing, and the run reported the
                # pop-up accepted while it still covered the form.
                heading = " ".join(text.split())[:60]
                attempts = (
                    lambda: self._click_resiliently(button, timeout_ms=4_000),
                    lambda: button.evaluate(
                        "e => { const inner = (e.shadowRoot && e.shadowRoot.querySelector('button')) || e;"
                        " inner.click(); }"),
                    lambda: (button.focus(), page.keyboard.press("Enter")),
                    lambda: page.get_by_role("button", name=agree).last.click(timeout=4_000, force=True),
                )
                for attempt in attempts:
                    try:
                        attempt()
                    except Exception:
                        continue
                    page.wait_for_timeout(1_500)
                    try:
                        still_there = dialog.is_visible() and bool(re.search(
                            r"privacy|consent|data protection", dialog.evaluate(
                                "e => ((e.getRootNode && e.getRootNode().host) || e).textContent || ''") or "",
                            re.IGNORECASE))
                    except Exception:
                        still_there = False  # the dialog was removed from the page
                    if not still_there:
                        logger.info("CONSENT: accepted %r", heading)
                        return True
                logger.warning("CONSENT: pressed Agree on %r but the pop-up is still showing", heading)
        except Exception as exc:
            logger.warning("Consent dialog check failed: %s", exc)
        return False

    def _fill_text_by_label(self, page: Page, label_fragment: str, value: str) -> bool:
        """Fills the text input/textarea that follows a question's own visible
        text, for forms where the label isn't DOM-associated with its field in
        any way _label_for's heuristics find -- fill_first_matching()
        (label-association based, same mechanism) silently found nothing here
        and failed with no log line at all. Same anchor technique as
        _answer_dropdown_by_label, which does work on this markup."""
        if not value:
            return False
        try:
            question = page.get_by_text(label_fragment, exact=False).first
            if question.count() == 0:
                logger.warning("No question text matching %r", label_fragment)
                return False
            field = question.locator(
                "xpath=following::input[@type='text' or not(@type)][1]"
                " | following::textarea[1]"
            ).first
            if field.count() == 0:
                logger.warning("No text field found after %r", label_fragment)
                return False
            current = (field.input_value() or "").strip()
            if current:
                logger.info("%r already holds %r; leaving it alone", label_fragment, current)
                return True
            if safety.is_attestation(label_fragment):
                logger.info("LEFT_FOR_YOU: %r is a signature/attestation -- the agent never fills it",
                            label_fragment[:70])
                return False
            field.fill(value, timeout=5_000)
            logger.info("Filled %r with %r", label_fragment, value[:60])
            return True
        except Exception as exc:
            logger.warning("Could not fill %r: %s", label_fragment, exc)
            return False

    @staticmethod
    def _question_label(page: Page, label_fragment: str):
        """The form's own <label>/<legend> whose text starts with the fragment.
        Deliberately not page.get_by_text(): on Ashby the job sidebar also says
        'Location', and anchoring there walks forward into the Name field."""
        pattern = re.compile(rf"^\s*{re.escape(label_fragment)}", re.IGNORECASE)
        label = page.locator("label, legend").filter(has_text=pattern).first
        return label if label.count() else None

    def _fill_typeahead_by_label(self, page: Page, label_fragment: str, value: str) -> bool:
        """Types into the combobox under a question label and clicks the
        suggestion. Typing alone doesn't register a choice on these widgets."""
        label = self._question_label(page, label_fragment)
        if label is None:
            logger.warning("No question label matching %r", label_fragment)
            return False
        field = label.locator("xpath=following::input[@role='combobox'][1]").first
        if field.count() == 0:
            logger.warning("No type-ahead field after %r", label_fragment)
            return False
        if (field.input_value() or "").strip():
            logger.info("%r already holds %r; leaving it alone", label_fragment, field.input_value())
            return True
        self.open_picker_control(page, field)
        field.type(value, delay=40)
        page.wait_for_timeout(1_500)
        if not self._click_visible_suggestion(page, field, value):
            field.press("ArrowDown")
            field.press("Enter")
        page.wait_for_timeout(800)
        committed = (field.input_value() or "").strip()
        logger.info("Type-ahead %r now holds %r", label_fragment, committed)
        return bool(committed)

    def _click_choice_button_by_label(self, page: Page, label_fragment: str, choice: str) -> bool:
        """Clicks the button whose text is exactly `choice` in the first row of
        buttons after a question label (Ashby's Yes/No toggles)."""
        label = self._question_label(page, label_fragment)
        if label is None:
            logger.warning("No question label matching %r", label_fragment)
            return False
        btn = label.locator(
            f"xpath=following::button[normalize-space(.)={json.dumps(choice)}][1]"
        ).first
        if btn.count() == 0:
            logger.warning("No %r button after %r", choice, label_fragment)
            return False
        # These are toggles: clicking an already-pressed 'No' UNselects it,
        # which is how a refill pass blanked a required answer.
        if btn.get_attribute("aria-pressed") == "true":
            logger.info("%r already answered %r; leaving it alone", label_fragment, choice)
            return True
        if self._click_resiliently(btn, timeout_ms=5_000):
            logger.info("Answered %r with %r", label_fragment, choice)
            return True
        return False

    def _answer_dropdown_by_label(self, page: Page, label_fragment: str, candidates: list[str]) -> bool:
        """Answers the dropdown whose question text contains `label_fragment`.

        Workday renders these as <button>s, not <select>s, and their ids are
        random, so the question text is the only stable handle. Skips a
        dropdown that already holds an answer rather than re-opening it."""
        # Walk forward from the question's own text to the next button, the
        # same way click_add_button anchors on a section heading. Reading an
        # ancestor's innerText instead spans SEVERAL questions: after the
        # first was answered 'No', every later question saw that 'No' in its
        # context and was skipped as 'already answered', which would have
        # left required questions blank.
        try:
            question = page.get_by_text(label_fragment, exact=False).first
            if question.count() == 0:
                logger.warning("No question text matching %r", label_fragment)
                return False
            # A native <select> must be handled with select_option(); the
            # button-dropdown path silently walks past it and answers whatever
            # widget comes next, which is how a work-authorisation question
            # ended up looking at a list of country dialling codes.
            native = question.locator("xpath=following::select[1]").first
            if native.count() and native.is_visible():
                return self._answer_native_select(native, label_fragment, candidates)

            btn = question.locator(
                "xpath=following::button[@aria-haspopup='listbox'][1]"
                " | following::*[@role='combobox'][1]"
            ).first
            if btn.count() == 0:
                logger.warning("No dropdown found after question %r", label_fragment)
                return False
            current = (btn.inner_text() or "").strip()
        except Exception as exc:
            logger.warning("Lookup failed for question %r: %s", label_fragment, exc)
            return False

        # Only leave an existing answer alone when it's one we actually want.
        # Treating ANY non-empty value as "already answered" meant a wrong or
        # stale answer could never be corrected.
        wanted = {c.strip().lower() for c in candidates}
        if current and current.lower() != "select one":
            if current.lower() in wanted:
                logger.info("Dropdown %r already answered with %r", label_fragment, current)
                return True
            logger.info(
                "Dropdown %r holds %r but %s was wanted -- changing it",
                label_fragment, current, candidates,
            )
        if self.select_from_button_dropdown(page, "", 0, candidates, locator=btn):
            logger.info("Answered %r with one of %s", label_fragment, candidates)
            return True
        return False

    def _answer_native_select(self, select, label_fragment: str, candidates: list[str]) -> bool:
        """Chooses an option in a native <select>, exact label first, then a
        semantic match against the options the page actually offers."""
        try:
            options = [
                o.strip() for o in select.locator("option").all_inner_texts() if o.strip()
            ]
            current = select.input_value()
            if current and current.lower() not in ("", "select...", "select one"):
                chosen = select.locator("option:checked").first
                text = (chosen.inner_text() or "").strip() if chosen.count() else current
                if text.lower() in {c.strip().lower() for c in candidates}:
                    logger.info("Dropdown %r already answered with %r", label_fragment, text)
                    return True

            for want_exact in (True, False):
                for cand in candidates:
                    for opt in options:
                        hit = (opt.lower() == cand.lower()) if want_exact else (cand.lower() in opt.lower())
                        if hit:
                            select.select_option(label=opt, timeout=5_000)
                            logger.info("Answered %r with %r (native select)", label_fragment, opt)
                            return True

            pick = self._match_option_semantically(candidates, options, label_fragment)
            if pick:
                select.select_option(label=pick, timeout=5_000)
                logger.info("Answered %r with %r (semantic)", label_fragment, pick)
                return True

            logger.warning(
                "No option matched %s for %r. Available: %s", candidates, label_fragment, options[:15]
            )
            return False
        except Exception as exc:
            logger.warning("Could not answer native select %r: %s", label_fragment, exc)
            return False

    def _check_box_by_label(self, page: Page, label_fragment: str) -> bool:
        if safety.is_attestation(label_fragment):
            logger.info("LEFT_FOR_YOU: %r is an attestation -- only you can agree to it", label_fragment[:70])
            return False
        """Ticks the checkbox whose own label reads `label_fragment` (e.g.
        'Hybrid'). Idempotent -- an already-ticked box is left alone."""
        want = label_fragment.strip().lower()

        def tick(box) -> bool:
            try:
                if box.is_checked():
                    return True
                box.check(timeout=5_000, force=True)
                logger.info("Ticked %r", label_fragment)
                return True
            except Exception as exc:
                logger.warning("Could not tick %r: %s", label_fragment, exc)
                return False

        # Pass 1: the checkbox's own label, exact then substring. Exact alone
        # missed a long consent label whose rendered text differs slightly
        # from the wording in the answers file.
        boxes = page.locator("input[type='checkbox']")
        fallback = None
        for i in range(min(boxes.count(), 60)):
            box = boxes.nth(i)
            try:
                if not box.is_visible():
                    continue
                label = (self._label_for(page, box.element_handle()) or "").strip().lower()
            except Exception:
                continue
            if label == want:
                return tick(box)
            if fallback is None and label and (want in label or label in want):
                fallback = box
        if fallback is not None:
            return tick(fallback)

        # Pass 2: anchor on the visible question text and take the checkbox
        # that follows it -- some labels aren't associated with the input in
        # any way _label_for can see.
        try:
            anchor = page.get_by_text(label_fragment, exact=False).first
            if anchor.count():
                box = anchor.locator("xpath=following::input[@type='checkbox'][1]").first
                if box.count():
                    return tick(box)
        except Exception as exc:
            logger.warning("Text-anchored lookup failed for %r: %s", label_fragment, exc)

        logger.warning("No checkbox found labelled %r", label_fragment)
        return False

    # ------------------------------------------------------------------
    # Page-shape questions, used by --auto mode to decide what to do next
    # ------------------------------------------------------------------
    def is_review_step(self, page: Page) -> bool:
        """True on the final review/summary step -- where auto mode stops and
        hands back to the human, because Submit is theirs to press."""
        try:
            if page.locator("button:text-is('Submit')").count():
                return True
            # Workday marks the active wizard step; 'Review' being current is
            # the same signal without depending on a button label.
            return page.locator(
                "[aria-current='step']:has-text('Review'), [data-automation-id='progressBarActiveStep']:has-text('Review')"
            ).count() > 0
        except Exception:
            return False

    _NEXT_SELECTORS = (
        "button:has-text('Save and Continue')",
        "button:has-text('Continue')",
        "button:has-text('Next')",
        "a:has-text('Save and Continue')",
        "a:has-text('Continue')",
    )

    @staticmethod
    def _in_popup(el) -> bool:
        try:
            return bool(el.evaluate("e => !!e.closest('[role=dialog], [aria-modal=true]')"))
        except Exception:
            return False

    def _wizard_button(self, page: Page, selectors=None):
        """First visible forward button that belongs to the page itself. A
        pop-up's Continue (a sign-in window, say) is not a wizard step: after
        a failed sign-in left one open, the agent clicked its Continue 24
        times in a row."""
        for selector in selectors or self._NEXT_SELECTORS:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 6)):
                el = loc.nth(i)
                try:
                    if safety.is_submit_label(el.inner_text()):
                        continue  # that button sends the application
                    if not el.is_visible() or self._in_popup(el):
                        continue
                    if not el.is_enabled():
                        # Google's Next stays disabled until the step is
                        # complete. Clicking it anyway spent the full 30s
                        # timeout per attempt and told the user nothing.
                        logger.info("The form's %r is disabled -- this step is not complete yet",
                                    (el.get_attribute("aria-label") or el.inner_text() or "Next").strip()[:30])
                        self._disabled_next = True
                        continue
                    return el
                except Exception:
                    continue

        # A Next drawn by script into a plain box. Casey's older ADP pages put
        # it in a <div class="appGo"> filled in after load, so a search for
        # buttons and links found nothing and the run took the first step for
        # the last. Only the default search, and only something clickable.
        if selectors is None:
            # "Update Profile" is how Schwab's (iCIMS) application leaves its
            # Candidate Profile step (1 of 5); its "Finish Later" is never used.
            exact = re.compile(r"^\s*(next|continue|save and continue|save & continue|next step|update profile)\s*$",
                               re.IGNORECASE)
            for candidate in (page.get_by_role("button", name=exact), page.get_by_text(exact)):
                try:
                    for i in range(min(candidate.count(), 6)):
                        el = candidate.nth(i)
                        if not el.is_visible() or self._in_popup(el) or not el.is_enabled():
                            continue  # a disabled Next means the step is not finished
                        if safety.is_submit_label(el.inner_text()):
                            continue
                        clickable = el.evaluate(
                            "e => { const s = getComputedStyle(e); return s.cursor === 'pointer'"
                            " || !!e.closest('[onclick], [role=button], a, button, input,"
                            " [class*=btn i], [class*=button i], [class*=Go]'); }")
                        if clickable:
                            return el
                except Exception:
                    continue
            # A privacy notice that is itself the way on. Schwab's sign-in step
            # (iCIMS) has one button: "I Acknowledge the Privacy Notice". The
            # owner lets the agent accept privacy notices
            # (accept_application_privacy_prompts); a legal declaration never
            # counts as one.
            if getattr(getattr(self, "_profile", None), "accept_application_privacy_prompts", False):
                consent = page.locator("input[type=submit], input[type=button], button, [role=button]")
                for i in range(min(consent.count(), 20)):
                    el = consent.nth(i)
                    try:
                        label = " ".join(((el.get_attribute("value") or "") + " " + (el.inner_text() or "") + " "
                                          + (el.get_attribute("aria-label") or "")).split())
                        if not re.search(r"\b(acknowledge|accept|agree)\b", label, re.IGNORECASE):
                            continue
                        if not safety.is_privacy_consent(label) or safety.is_attestation(label) \
                                or safety.is_submit_label(label):
                            continue
                        if not el.is_visible() or self._in_popup(el) or not el.is_enabled():
                            continue
                        logger.info("Next page: accepting the privacy notice (%r)", label[:50])
                        return el
                    except Exception:
                        continue
            # A next-page arrow with no words at all. Casey's ADP pages draw
            # their Next as a box whose only label is a right-arrow icon from
            # an icon font (drawn with CSS, not text), so nothing reading
            # "Next" existed and the run stopped on step three of five.
            try:
                arrow = page.evaluate("""() => {
                    const arrows = ['\\uf061', '\\uf054', '\\uf105', '\\uf138', '\\u2192', '\\u203a', '\\u00bb', '\\u276f'];
                    const glyph = s => (s || '').replace(/["']/g, '');
                    const hits = [...document.querySelectorAll('div, span, a, button, i')].filter(e => {
                        if (!e.getClientRects().length) return false;
                        if ((e.innerText || '').trim().replace(/[\\u2192\\u203a\\u00bb\\u276f>]/g, '')) return false;
                        const marks = [glyph(getComputedStyle(e, '::after').content),
                                       glyph(getComputedStyle(e, '::before').content),
                                       (e.innerText || '').trim()];
                        if (!marks.some(m => arrows.includes(m))) return false;
                        const box = e.closest('[onclick], a, button, [role=button]') || e;
                        return getComputedStyle(box).cursor === 'pointer' || box !== e;
                    });
                    if (!hits.length) return null;
                    const target = hits[hits.length - 1];
                    target.setAttribute('data-agent-next-arrow', '1');
                    return true;
                }""")
                if arrow:
                    el = page.locator("[data-agent-next-arrow='1']").last
                    if el.count() and el.is_visible() and not self._in_popup(el):
                        logger.info("Next page: using the form's arrow button")
                        return el
            except Exception as exc:
                logger.debug("Arrow search failed: %s", str(exc).splitlines()[0][:100])
        return None

    def _page_fingerprint(self, page: Page) -> str:
        try:
            # The values matter as much as the fields: a questionnaire filled
            # in is a changed page, and without them a form that had refused to
            # advance stayed marked as the last step however much was answered.
            return page.url + "|" + page.evaluate(
                "() => [...document.querySelectorAll('h1, h2, h3, input, select, textarea')]"
                ".filter(e => e.getClientRects().length)"
                ".map(e => (e.id || e.name || e.innerText || e.tagName) + ':' + ((e.value || '').slice(0, 24)))"
                ".join(',').slice(0, 6000)"
            )
        except Exception:
            return page.url

    def has_next_step(self, page: Page) -> bool:
        """True when there's a forward button to advance the wizard -- and the
        last click on one actually moved somewhere. A click that left the same
        page in place means it isn't a wizard step, so stop advancing."""
        if getattr(self, "_stuck_on", None) == self._page_fingerprint(page):
            # Before calling it the end: the page may be waiting for a section
            # of its own to be saved, which is what Dayforce does. If one was
            # open, saving it is the reason Next did nothing.
            if self.commit_open_sections(page):
                self._stuck_on = None
                return self._wizard_button(page) is not None
            logger.warning("NEXT_STUCK: clicking Next/Continue didn't change the page -- treating it as the last step")
            return False
        # 'Save and Submit' is deliberately NOT here: that button submits.
        return self._wizard_button(page) is not None

    def has_experience_section(self, page):
        """Workday-specific; implemented in sites/workday.py."""
        page_ref = page
        adapter = self.adapter(page_ref) if hasattr(page_ref, 'url') else self.adapter(self._page_hint())
        handler = getattr(adapter, 'has_experience_section', None)
        if handler is None:
            logger.info('No has_experience_section handler for this site')
            return None
        return handler(self, page)


    def _sign_in_scope(self, page: Page):
        """A visible Sign In form, when one is present.

        These sites open Sign In as a modal ON TOP of the Create Account
        page, so a page-wide 'how many password fields are there' count sees
        both forms at once (3 fields) and picks the wrong one -- filling the
        obscured Create Account form while the modal swallows every click."""
        for selector in (
            "form[data-automation-id^='signInForm']",
            "form[data-automation-id*='signInForm']",
            "[data-behavior-click-outside-close='topmost'] form",
        ):
            loc = page.locator(selector).first
            try:
                if loc.count() and loc.is_visible():
                    return loc
            except Exception:
                continue
        return None

    def _goto_login_page(self, page: Page) -> bool:
        """Navigates to the tenant's standalone login page, carrying the
        current page as the post-login redirect.

        Workday's URLs are /<locale>/<site>/..., so the login page is
        /<locale>/<site>/login. Returns False if the URL doesn't look like
        that, leaving the caller on whatever page it was already on."""
        parsed = urlparse(page.url)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2 or parts[2:3] == ["login"]:
            return False
        target = (
            f"{parsed.scheme}://{parsed.netloc}/{parts[0]}/{parts[1]}"
            f"/login?redirect={quote(parsed.path, safe='')}"
        )
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(3_000)
            logger.info("Navigated to the standalone login page")
            return True
        except Exception as exc:
            logger.warning("Could not open the login page: %s", exc)
            return False

    def sign_in_from_header(self, page: Page, email: str) -> bool:
        """Signs in through a site-header 'Sign In' link (Eightfold/CBTS style)
        when the application form itself shows no login: click it, enter the
        ATS email, Continue, enter ATS_PASSWORD, submit. Tried once per site
        per run. Returns True only when the header no longer offers Sign In.

        Stops and hands over when the site asks for an emailed one-time code
        (the agent has no inbox access) or says there's no account yet."""
        host = urlparse(page.url).netloc.lower()
        if not safety.password_allowed(page.url):
            return False
        tried = self.__dict__.setdefault("_header_signin_hosts", set())
        if host in tried:
            return False
        password = self._read_ats_password()
        config = getattr(self, "_config", None)
        email = (getattr(config, "ats_email", "") or email or "").strip()
        if not password or not email:
            return False

        link_name = re.compile(r"^\s*(sign in|log in|login)\s*$", re.IGNORECASE)

        def header_link():
            for role in ("link", "button"):
                loc = page.get_by_role(role, name=link_name)
                for i in range(min(loc.count(), 5)):
                    el = loc.nth(i)
                    try:
                        if el.is_visible() and not el.evaluate(
                            "e => !!e.closest('form, [role=dialog], [aria-modal=true]')"
                        ):
                            return el
                    except Exception:
                        continue
            return None

        link = header_link()
        if link is None:
            return False
        tried.add(host)
        logger.info("LOGIN: clicking the site's Sign In link on %s", host)
        if not self._click_resiliently(link, timeout_ms=5_000):
            return False
        page.wait_for_timeout(2_500)

        dialog = page.locator("[role=dialog], [aria-modal=true]").filter(
            has=page.locator("input[type=email], input[name*=email i], input[id*=email i]")
        )
        scope = dialog.last if dialog.count() else page

        # The user's choice (2026-09-15): when the site offers Google sign-in,
        # use it. Only Google's account picker is driven -- a Google password
        # or 2-step prompt is always left to the user.
        google = self.find_google_sign_in(scope)
        if google is not None:
            return self._sign_in_with_google(page, google, email, host, header_link)

        email_box = self._find_login_email_input(scope)
        if email_box is None:
            logger.warning("LOGIN: Sign In opened but no email box was found on %s", host)
            return False

        def type_into(box, value):
            box.click()
            box.fill("")
            box.press_sequentially(value, delay=15)  # keystrokes: React forms ignore fill()

        def click_named(pattern: str) -> bool:
            btn = scope.get_by_role("button", name=re.compile(pattern, re.IGNORECASE))
            for i in range(btn.count()):
                if btn.nth(i).is_visible():
                    return self._click_resiliently(btn.nth(i), timeout_ms=5_000)
            return False

        type_into(email_box, email)
        pw = scope.locator("input[type=password]").first
        if not (pw.count() and pw.is_visible()):
            if not click_named(r"^\s*(continue|next)\s*$"):
                email_box.press("Enter")
            try:
                pw.wait_for(state="visible", timeout=10_000)
            except Exception:
                text = (scope.inner_text() or "").lower()
                if re.search(r"\bcode\b|one[- ]time|verification|check your (email|inbox)", text):
                    logger.warning(
                        "LOGIN_NEEDS_CODE: %s emailed a sign-in code to %s -- enter it in the open "
                        "browser window; the agent can't read your inbox", host, email,
                    )
                elif re.search(r"no account|don't recognize|create (a new |an )?account|not (registered|found)|sign up", text):
                    logger.warning("LOGIN_NO_ACCOUNT: %s has no account for %s -- closing the sign-in window",
                                   host, email)
                    self._close_popup(page, scope)
                else:
                    logger.warning("LOGIN: no password step appeared on %s (%s)", host, text.strip()[:120])
                return False

        type_into(pw, password)
        # A real Sign In button first: the email step's Continue can still be
        # on screen, and clicking it again restarts the email step instead.
        if not (click_named(r"^\s*(sign in|log in|login)\s*$")
                or click_named(r"^\s*(continue|submit)\s*$")):
            pw.press("Enter")
        page.wait_for_timeout(5_000)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:
            pass

        if header_link() is None:
            logger.info("LOGIN_OK: signed in on %s as %s", host, email)
            return True
        error = ""
        try:
            error = " / ".join(
                t.strip() for t in page.locator("[role=alert], [class*=error i]").all_inner_texts() if t.strip()
            )[:200]
        except Exception:
            pass
        logger.warning("LOGIN_FAILED: still signed out on %s%s", host, f" ({error})" if error else "")
        return False

    def _close_popup(self, page: Page, scope) -> None:
        """Closes a pop-up with its own Close button; Escape alone left the
        CBTS sign-in window open over the form."""
        try:
            close = scope.get_by_role("button", name=re.compile(r"\bclose\b|^\s*(cancel|dismiss)\s*$", re.IGNORECASE))
            for i in range(close.count()):
                if close.nth(i).is_visible():
                    self._click_resiliently(close.nth(i), timeout_ms=3_000)
                    page.wait_for_timeout(600)
                    return
            page.keyboard.press("Escape")
        except Exception as exc:
            logger.warning("Could not close pop-up: %s", exc)

    def find_google_sign_in(self, scope):
        """The site's own "sign in with Google" control, however it is drawn.

        ADP offers Google as a bare icon -- a red G under "Or sign in using
        social media", an <img alt="Google"> with no words -- so a search for
        "Sign in with Google" found nothing and the run filled the email box
        and stopped. Only Google is looked for: LinkedIn, Indeed and Facebook
        sit beside it and are never used.
        """
        wordings = (
            re.compile(r"(sign in|continue|log in|sign up) (with|using) google", re.IGNORECASE),
            re.compile(r"^\s*google\s*$", re.IGNORECASE),
        )
        for name in wordings:
            for role in ("button", "link"):
                try:
                    found = scope.get_by_role(role, name=name)
                    for i in range(min(found.count(), 3)):
                        if found.nth(i).is_visible():
                            return found.nth(i)
                except Exception:
                    continue
        # An icon with no accessible name of its own: find the picture, click
        # what it sits in.
        for selector in ("[id=google]", "[label=google i]", "[aria-label*=google i]",
                         "img[alt=google i]", "img[src*=google i]"):
            try:
                found = scope.locator(selector)
                for i in range(min(found.count(), 3)):
                    candidate = found.nth(i)
                    if not candidate.is_visible():
                        continue
                    if (candidate.evaluate("e => e.tagName") or "").upper() == "IMG":
                        wrapper = candidate.locator("xpath=ancestor::*[self::a or self::button][1]").first
                        if wrapper.count():
                            return wrapper
                    return candidate
            except Exception:
                continue
        return None

    def sign_in_with_google_if_offered(self, page: Page, email: str) -> bool:
        """Takes the Google option on a page whose whole purpose is signing in.

        The user's choice (2026-09-15): where a site offers Google, use it. The
        route through a header "Sign In" link did that; a sign-in *page* --
        ADP's "Welcome!" -- has no such link, so Google was never tried there.
        Once per site per run.
        """
        host = urlparse(page.url).netloc.lower()
        tried = self.__dict__.setdefault("_google_signin_hosts", set())
        if host in tried:
            return False
        button = self.find_google_sign_in(page)
        if button is None:
            return False
        tried.add(host)
        config = getattr(self, "_config", None)
        email = (getattr(config, "ats_email", "") or email or "").strip()
        # Signed in once the site stops offering Google sign-in.
        return self._sign_in_with_google(page, button, email, host,
                                         lambda: self.find_google_sign_in(page))

    def _sign_in_with_google(self, page: Page, button, email: str, host: str, header_link) -> bool:
        """Clicks 'Sign in using Google' and picks the user's account on
        Google's chooser. Never types a Google password: if Google asks for
        one (or for 2-step verification, or blocks the automated browser),
        that window is left for the user and the agent waits for them."""
        context = page.context
        pages_before = set(context.pages)
        logger.info("LOGIN: using the site's Google sign-in on %s", host)
        self._click_resiliently(button, timeout_ms=5_000)
        page.wait_for_timeout(3_000)
        new_pages = [pg for pg in context.pages if pg not in pages_before]
        google = new_pages[-1] if new_pages else page
        try:
            google.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:
            pass

        if not google.is_closed() and "accounts.google.com" in google.url:
            chooser = google.get_by_text(re.compile(re.escape(email), re.IGNORECASE))
            if email and chooser.count() and chooser.first.is_visible():
                self._click_resiliently(chooser.first, timeout_ms=5_000)
                logger.info("LOGIN: picked %s on Google's account chooser", email)
                # Google's pop-up usually closes itself once the account is
                # picked, so wait on the site's page, never on the pop-up.
                page.wait_for_timeout(3_000)
                for label in ("Continue", "Allow"):
                    try:
                        if google.is_closed():
                            break
                        btn = google.get_by_role("button", name=label)
                        if btn.count() and btn.first.is_visible():
                            self._click_resiliently(btn.first, timeout_ms=5_000)
                            page.wait_for_timeout(2_000)
                    except Exception:
                        break  # the pop-up closed mid-check

        # Give Google -- and the user, if Google wants a password or a 2-step
        # code -- up to 3 minutes to hand back to the site.
        deadline = time.time() + 180
        warned = False
        while time.time() < deadline:
            google_done = google is page or google is getattr(page, "top", None) or google.is_closed()
            if not page.is_closed() and host in page.url and google_done:
                # Signing in reloads the site; let it settle before judging.
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=15_000)
                    page.wait_for_timeout(4_000)
                except Exception:
                    pass
                if header_link() is None:
                    logger.info("LOGIN_OK: signed in on %s with Google", host)
                    self.remember_account(page, email, "google")
                    return True
            if not warned and not google.is_closed() and "accounts.google.com" in google.url:
                logger.warning(
                    "LOGIN_NEEDS_GOOGLE: Google is asking you to sign in -- finish it in the open "
                    "browser window (the agent never types your Google password). Waiting up to 3 minutes."
                )
                warned = True
            time.sleep(3)
        logger.warning("LOGIN_FAILED: Google sign-in didn't complete on %s", host)
        return False

    def handle_auth_gate(self, page: Page, email: str) -> bool:
        """If the page is sitting on an employer ATS account gate, get past
        it: two password fields means 'Create Account', one means 'Sign
        In'. Does nothing when no password field is present, or when no
        ATS_PASSWORD has been supplied."""
        try:
            # A sign-in form wins outright: the account exists by then, and
            # re-registering just bounces back to this same gate.
            scope = self._sign_in_scope(page)
            if scope is not None and self._read_ats_password():
                # Sign In opens as a modal ON TOP of Create Account, and that
                # stacked state is unreliable to drive (fields clear, clicks
                # land on the form underneath). The dedicated login page has
                # exactly one form and signs in cleanly, so go there instead.
                if page.locator("button:has-text('Create Account')").count() and self._goto_login_page(page):
                    scope = self._sign_in_scope(page)
                logger.info("Sign In form present; signing in rather than creating an account")
                return self.attempt_auto_login(page, email, "", scope=scope)

            pw_count = page.locator("input[type='password']").count()
            if pw_count == 0:
                return False
            if not self._read_ats_password():
                return False
            if pw_count >= 2:
                if self.account_on_record():
                    return False  # already have one here: two password-type boxes are something else (a passcode step)
                self._create_form_attempted = False
                if self.fill_create_account_form(page, email):
                    return True
                # The older Workday routine ticks every checkbox; only fall back
                # to it when the careful filler didn't recognise the form at all.
                return False if self._create_form_attempted else self.create_ats_account(page, email)
            return self.attempt_auto_login(page, email, "")
        except BlockedLoginDomainError as exc:
            logger.error("LOGIN_BLOCKED: %s", exc)
            return False
        except Exception as exc:
            logger.warning("Auth gate handling failed: %s", exc)
            return False

    def apply_dropdown_answers(self, page: Page) -> None:
        """Applies human-supplied answers to button-triggered dropdowns and
        checkboxes, read from data/_dropdown_answers.json:

            {"dropdowns": {id_fragment: answer}, "checkboxes": [id_fragment]}

        These are questions a human decided the answer to (work
        authorization, demographic disclosures, policy attestations)
        rather than anything inferred -- the file is the handoff point, so
        answers can be supplied without restarting this process."""
        answers_path = Path("data/_dropdown_answers.json")
        if not answers_path.exists():
            return
        try:
            # utf-8-sig: a BOM (which PowerShell's Set-Content writes by
            # default) otherwise makes json.loads reject the whole file, and
            # every human-supplied answer is silently skipped.
            data = json.loads(answers_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            logger.warning("Could not read dropdown answers: %s", exc)
            return

        # Diagnostic escape hatch. apply_flow.py's signal dispatcher is not
        # hot-reloadable, so this file -- which is -- carries the hook for
        # inspecting a widget's real markup mid-run instead of guessing at it.
        for id_suffix in data.get("dump_widgets", []):
            self.dump_widget_html(page, id_suffix)

        if data.get("dump_page"):
            self.dump_page_shape(page)

        for label_fragment, path in data.get("attach_files", {}).items():
            self.upload_named_file(page, label_fragment, path)

        # Navigation escape hatch (e.g. ["Back"]) -- apply_flow's signal
        # dispatcher isn't hot-reloadable, so going back a wizard step to
        # correct an answer would otherwise need a full restart.
        target_url = data.get("goto", "")
        if target_url and page.url != target_url:
            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(4_000)
                logger.info("Navigated to %s", target_url)
            except Exception as exc:
                logger.warning("Could not navigate to %s: %s", target_url, exc)

        for button_text in data.get("click_buttons", []):
            try:
                # text-is, not has-text: 'Back' matched 'Back to Job Posting'
                # and left the wizard entirely. Links count too -- a saved
                # application is resumed from a link, not a button.
                btn = page.locator(
                    f"button:text-is('{button_text}'), a:text-is('{button_text}')"
                ).first
                if btn.count() and self._click_resiliently(btn, timeout_ms=5_000):
                    page.wait_for_timeout(2_500)
                    logger.info("Clicked %r", button_text)
                else:
                    logger.warning("Button %r not found", button_text)
            except Exception as exc:
                logger.warning("Could not click %r: %s", button_text, exc)

        answers = data.get("dropdowns", data if "checkboxes" not in data else {})

        # Answers keyed by visible label text, for employer-specific questions
        # that have no stable id and that Claude refuses to answer because
        # nothing in the resume grounds them (salary expectations, shift
        # preferences). The human supplies these; nothing here is inferred.
        for label_fragment, value in data.get("by_label", {}).items():
            try:
                self._fill_text_by_label(page, label_fragment, value)
            except Exception as exc:
                logger.warning("Could not answer %r: %s", label_fragment, exc)

        # Dropdowns addressed by their question text, for human-supplied
        # answers to questions with no stable id (work authorisation,
        # relocation, background declarations).
        for label_fragment, candidates in data.get("label_dropdowns", {}).items():
            try:
                self._answer_dropdown_by_label(page, label_fragment, candidates)
            except Exception as exc:
                logger.warning("Could not answer dropdown %r: %s", label_fragment, exc)

        # Type-ahead comboboxes addressed by their question label (Ashby's
        # Location has no id or name at all).
        for label_fragment, value in data.get("label_typeahead", {}).items():
            try:
                self._fill_typeahead_by_label(page, label_fragment, value)
            except Exception as exc:
                logger.warning("Could not fill type-ahead %r: %s", label_fragment, exc)

        # Questions answered by clicking one of a row of buttons (Ashby's
        # Yes/No toggles), addressed by question label.
        for label_fragment, choice in data.get("label_buttons", {}).items():
            try:
                self._click_choice_button_by_label(page, label_fragment, choice)
            except Exception as exc:
                logger.warning("Could not answer %r: %s", label_fragment, exc)

        for label_fragment in data.get("label_checkboxes", []):
            try:
                self._check_box_by_label(page, label_fragment)
            except Exception as exc:
                logger.warning("Could not tick %r: %s", label_fragment, exc)

        # Undo a wrong fill: {"clear_fields": [id or name fragment, ...]}
        for fragment in data.get("clear_fields", []):
            try:
                loc = page.locator(f"input[id*={json.dumps(fragment)} i], input[name*={json.dumps(fragment)} i], "
                                   f"textarea[id*={json.dumps(fragment)} i], select[id*={json.dumps(fragment)} i]")
                for i in range(loc.count()):
                    el = loc.nth(i)
                    if el.evaluate("e => e.tagName") == "SELECT":
                        el.select_option(index=0)
                    else:
                        el.fill("")
                    logger.info("Cleared wrongly filled field %r", fragment)
            except Exception as exc:
                logger.warning("Could not clear %r: %s", fragment, exc)

        for id_suffix, value in data.get("text_fields", {}).items():
            try:
                field = page.locator(f"input[id$='{id_suffix}'], textarea[id$='{id_suffix}']").first
                if field.count() == 0 or (field.input_value() or "").strip():
                    continue
                field.fill(value, timeout=5_000)
                logger.info("Filled text field %r", id_suffix)
            except Exception as exc:
                logger.warning("Could not fill text field %r: %s", id_suffix, exc)

        # "choose_one": the control is a dropdown -- pick one of ITS options.
        # Kept separate from text_fields on purpose: typing a value into a
        # dropdown leaves it looking answered while holding nothing.
        for id_suffix, candidates in data.get("choose_one", {}).items():
            try:
                self.select_one_option(
                    page, id_suffix, candidates if isinstance(candidates, list) else [candidates]
                )
            except Exception as exc:
                logger.warning("Could not answer dropdown %r: %s", id_suffix, exc)

        for id_suffix, candidates in data.get("searchable", {}).items():
            try:
                field = page.locator(f"input[id$='{id_suffix}']").first
                if field.count() == 0:
                    continue
                # A multiselect's input.value is ALWAYS '' -- the value lives
                # in a pill -- so the old input_value() guard never fired and
                # every pass re-drove an already-answered field.
                if self._multiselect_selection(page, id_suffix):
                    continue
                self.select_from_searchable_input(
                    page, id_suffix, candidates if isinstance(candidates, list) else [candidates]
                )
            except Exception as exc:
                logger.warning("Could not fill searchable %r: %s", id_suffix, exc)

        for id_fragment in data.get("checkboxes", []):
            try:
                box = page.locator(f"input[type='checkbox'][id*='{id_fragment}']").first
                if box.count() == 0 or box.is_checked():
                    continue
                box.check(timeout=5_000)
                logger.info("Checked box %r", id_fragment)
            except Exception as exc:
                logger.warning("Could not check box %r: %s", id_fragment, exc)

        for id_fragment, answer in answers.items():
            try:
                btn = page.locator(f"button[id*='{id_fragment}']").first
                if btn.count() == 0:
                    continue
                current = (btn.inner_text() or "").strip()
                # Re-answer whenever the shown value differs from the
                # intended one -- not just when it's still unset -- so a
                # corrected answer actually replaces a previous one.
                if current and current.strip().lower() == str(answer).strip().lower():
                    continue  # already the intended answer
                if self.select_from_button_dropdown(page, "", 0, [answer], locator=btn):
                    logger.info("Answered dropdown %r with %r", id_fragment, answer)
            except Exception as exc:
                logger.warning("Could not answer dropdown %r: %s", id_fragment, exc)

    def upload_named_file(self, page: Page, label_fragment: str, file_path: Path | str) -> bool:
        """Attaches a file to the upload control belonging to a given label.

        Forms often have several file inputs (resume, cover letter,
        transcripts); taking the first one puts the cover letter in the resume
        slot. This walks forward from the label's own text instead."""
        path = Path(file_path)
        if not path.is_file():
            return False
        try:
            anchor = page.get_by_text(label_fragment, exact=False).first
            if anchor.count() == 0:
                logger.info("No %r upload control on this form", label_fragment)
                return False
            file_input = anchor.locator("xpath=following::input[@type='file'][1]").first
            if file_input.count() == 0:
                return False
            # Already populated? Leave it be rather than stacking duplicates.
            existing = file_input.evaluate("el => el.files && el.files.length")
            if existing:
                return True
            file_input.set_input_files(str(path), timeout=15_000)
            page.wait_for_timeout(1_500)
            logger.info("Attached %s as %s", path.name, label_fragment)
            return True
        except Exception as exc:
            logger.warning("Could not attach %s: %s", label_fragment, exc)
            return False

    @staticmethod
    def _file_already_attached(page: Page, filename: str) -> bool:
        """True when the form visibly lists `filename` as an attachment."""
        try:
            shown = page.get_by_text(filename, exact=False)
            if any(shown.nth(i).is_visible() for i in range(min(shown.count(), 5))):
                return True
            # Signed in on CBTS, the chosen resume shows as a dropdown's value
            # rather than as text, and each pass uploaded another copy.
            return bool(page.evaluate(
                "name => [...document.querySelectorAll('input, select')]"
                "          .some(e => (e.value || '').includes(name))"
                "     || (document.body ? document.body.textContent.includes(name) : false)",
                filename,
            ))
        except Exception:
            return False

    def autofill_from_resume(self, page: Page, resume_path: Path) -> bool:
        """Hands the resume to the site's own 'Autofill from resume' box, when
        the form has one, BEFORE the agent fills anything. The ATS parses it
        into name/email/phone/links (and usually attaches it as the Resume),
        so the agent only has to correct and complete what's left.

        Only acts on an upload box that sits next to an autofill prompt --
        never a 'Continue with LinkedIn/Indeed' button, and never Workday's
        chooser page (dismiss_apply_chooser still picks Apply Manually there,
        because its parsed work history collides with fill_experience_section)."""
        target = Path(resume_path)
        if not target.is_file():
            return False
        self._resume_path = target
        if self._file_already_attached(page, target.name):
            return False  # already done on an earlier pass
        # SuccessFactors application: uploading the resume makes the site parse
        # it and rebuild the form, wiping anything answered before. So it goes
        # first, before a single field is touched.
        if self.adapter(page).attachment_is_empty(page, "resume"):
            if self.upload_via_chooser(page, r"^\s*(upload|attach|add) (a |your )?(resume|cv)\s*$", target):
                page.wait_for_timeout(6_000)
                self.expand_all_sections(page)
                return True
        try:
            prompt = page.get_by_text(
                re.compile(r"autofill (from|with) (your |my )?resume", re.IGNORECASE)
            ).first
            if prompt.count() == 0 or not prompt.is_visible():
                return False
            # The prompt's own upload box: the closest ancestor holding exactly
            # one file input. Stop if it widens to several -- that is the whole
            # form, and its first file input may be the cover letter.
            file_input = None
            for depth in range(1, 7):
                inputs = prompt.locator(f"xpath=ancestor::*[{depth}]").locator("input[type='file']")
                n = inputs.count()
                if n == 1:
                    file_input = inputs.first
                    break
                if n > 1:
                    break
            if file_input is None:
                logger.info("Autofill prompt found but no upload box beside it")
                return False

            count_filled = (
                "() => [...document.querySelectorAll('input, textarea, select')]"
                ".filter(e => !['hidden','file','submit','button','radio','checkbox'].includes(e.type)"
                " && (e.offsetParent || e.getClientRects().length) && (e.value || '').trim()).length"
            )
            before = page.evaluate(count_filled)
            file_input.set_input_files(str(target), timeout=15_000)
            logger.info("AUTOFILL: gave %s to the site's resume autofill", target.name)
            try:
                page.get_by_text(
                    re.compile(r"autofill (completed|complete|finished|done)", re.IGNORECASE)
                ).first.wait_for(state="visible", timeout=25_000)
            except Exception:
                logger.info("AUTOFILL: no completion message within 25s; continuing")
            page.wait_for_timeout(1_500)  # let the parsed values settle into the inputs
            # Worth knowing per site: some parsers (Ashby, for one tenant at
            # least) report success but fill nothing, leaving it all to us.
            logger.info("AUTOFILL: site filled %d field(s)", page.evaluate(count_filled) - before)
            return True
        except Exception as exc:
            logger.warning("Resume autofill failed: %s", exc)
            return False

    @staticmethod
    def _cover_letter_control(page: Page):
        """('file' | 'text', locator) for the form's cover-letter field, or
        None when the form has nowhere to put one. Anchors on a visible
        'Cover Letter' heading and takes the one upload box or text box in
        its own field container -- stopping as soon as the container widens
        to hold several, since that is the whole form (and the resume slot)."""
        try:
            headings = page.locator("label, legend, h2, h3, h4, span, div, p").filter(
                has_text=re.compile(r"^\s*cover letter\s*\*?\s*$", re.IGNORECASE)
            )
            for i in range(min(headings.count(), 8)):
                heading = headings.nth(i)
                if not heading.is_visible():
                    continue
                for depth in range(1, 6):
                    container = heading.locator(f"xpath=ancestor::*[{depth}]")
                    files = container.locator("input[type='file']")
                    boxes = container.locator("textarea")
                    n_files, n_boxes = files.count(), boxes.count()
                    if n_files > 1 or n_boxes > 1 or (n_files and n_boxes):
                        break
                    if n_files == 1:
                        return "file", files.first
                    if n_boxes == 1:
                        return "text", boxes.first
        except Exception as exc:
            logger.warning("Cover letter field lookup failed: %s", exc)
        return None

    _COVER_TILE = r"^\s*(attach|upload|add) (a |your )?cover letter\s*$"

    def has_cover_letter_field(self, page: Page) -> bool:
        if self._cover_letter_control(page) is not None:
            return True
        tile = page.locator("a, button, [role=button], div, span").filter(has_text=re.compile(self._COVER_TILE, re.IGNORECASE))
        return any(tile.nth(i).is_visible() for i in range(min(tile.count(), 5)))

    def attach_cover_letter(self, page: Page, letter_txt: Path, letter_pdf: Path) -> bool:
        """Uploads the letter PDF, or pastes the text into a cover-letter text
        box. Leaves the field alone if it already holds the letter."""
        found = self._cover_letter_control(page)
        if found is None:
            if self._file_already_attached(page, Path(letter_pdf).name):
                return True
            return self.upload_via_chooser(page, self._COVER_TILE, Path(letter_pdf))
        kind, control = found
        try:
            if kind == "file":
                if self._file_already_attached(page, Path(letter_pdf).name):
                    return True
                control.set_input_files(str(letter_pdf), timeout=15_000)
                page.wait_for_timeout(1_500)
                logger.info("Attached cover letter: %s", Path(letter_pdf).name)
            else:
                if (control.input_value() or "").strip():
                    logger.info("Cover letter box already has text; leaving it alone")
                    return True
                control.fill(Path(letter_txt).read_text(encoding="utf-8"), timeout=5_000)
                logger.info("Pasted cover letter into the form's text box")
            return True
        except Exception as exc:
            logger.warning("Could not attach cover letter: %s", exc)
            return False

    def find_required_blanks(self, page: Page) -> dict:
        """Required fields that are still empty, plus any error messages the
        form is showing -- the list of things left for the agent or the human
        after autofill and field-filling have both run."""
        try:
            return page.evaluate(
                """() => {
                    const visible = e => !!(e.offsetParent || e.getClientRects().length);
                    // The nearest question label/legend that isn't inside the
                    // control itself, walking up from `start`.
                    const questionLabel = (start, exclude) => {
                        let n = start;
                        for (let i = 0; i < 5 && n; i++, n = n.parentElement) {
                            const l = [...n.querySelectorAll('label, legend')]
                                .find(x => !exclude.contains(x) && x.innerText.trim());
                            if (l) return l;
                        }
                        return null;
                    };
                    const labelOf = el => {
                        // Custom dropdowns name their question via ARIA; without
                        // this every one fell back to its 'Select' placeholder
                        // and several blanks collapsed into one entry.
                        const ids = (el.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean);
                        const byIds = ids.map(i => document.getElementById(i)).filter(Boolean);
                        if (byIds.length) return byIds[0];
                        if (el.id && el.type !== 'radio') {
                            const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                            if (l && l.innerText.trim()) return l;
                        }
                        if (el.getAttribute('aria-label')) return null;  // named by aria-label itself
                        return questionLabel(el.parentElement, el);
                    };
                    // Ashby (and others) draw the asterisk with CSS ::after, so
                    // it never appears in innerText -- check styles and classes too.
                    const markedRequired = l => !!l && (
                        /\\*\\s*$/.test(l.innerText)
                        || (getComputedStyle(l, '::after').content || '').includes('*')
                        || (getComputedStyle(l, '::before').content || '').includes('*')
                        || /required/i.test(l.className || '')
                        || !!l.querySelector('[class*=required i], abbr[title*=required i]')
                    );
                    const isRequired = (el, l) =>
                        el.required || el.getAttribute('aria-required') === 'true' || markedRequired(l);
                    const ariaName = el => el.getAttribute('aria-label') || '';
                    const text = (l, el) =>
                        (l ? l.innerText.trim() : (ariaName(el) || el.placeholder || el.name || '')).slice(0, 120);

                    const blanks = [];
                    const seenGroups = new Set();
                    for (const el of document.querySelectorAll('input, textarea, select')) {
                        const type = (el.type || '').toLowerCase();
                        if (['hidden', 'submit', 'button', 'file', 'checkbox'].includes(type)) continue;
                        if (type === 'radio') {
                            if (!el.name || seenGroups.has(el.name)) continue;
                            seenGroups.add(el.name);
                            const group = [...document.querySelectorAll(`input[type=radio][name="${CSS.escape(el.name)}"]`)];
                            const container = el.closest('fieldset, [role=radiogroup]');
                            const l = container
                                ? [...container.querySelectorAll('legend, label')].find(x => !group.some(r => x.htmlFor === r.id))
                                : questionLabel(el.parentElement?.parentElement?.parentElement, el.parentElement);
                            if (group.some(r => isRequired(r, l)) && !group.some(r => r.checked)) blanks.push(text(l, el));
                            continue;
                        }
                        if (!visible(el)) continue;
                        const l = labelOf(el);
                        let value = (el.value || '').trim();
                        if (!value) {
                            // A react-select combobox clears its search input
                            // once you choose; the choice sits beside it.
                            let n = el.parentElement;
                            for (let i = 0; i < 4 && n && !value; i++, n = n.parentElement) {
                                if (n.querySelectorAll('input:not([type=hidden]), select, textarea').length > 1) break;
                                const shown = n.querySelector('[class*=singleValue], [class*=single-value],'
                                                            + '[class*=multiValue], [class*=multi-value],'
                                                            + '[class*=selection-item]:not([class*=search])');
                                if (shown) value = shown.innerText.trim();
                            }
                        }
                        if (isRequired(el, l) && !value) blanks.push(text(l, el));
                    }
                    // Yes/No-style toggle buttons (Ashby): a row of aria-pressed
                    // buttons with none pressed.
                    const rows = new Set([...document.querySelectorAll('button[aria-pressed]')].filter(visible).map(b => b.parentElement));
                    for (const row of rows) {
                        const buttons = [...row.querySelectorAll('button[aria-pressed]')];
                        if (buttons.length < 2) continue;
                        const l = questionLabel(row.parentElement, row);
                        if (markedRequired(l) && !buttons.some(b => b.getAttribute('aria-pressed') === 'true')) {
                            blanks.push(text(l, row));
                        }
                    }
                    // A success notice often shares the alert role with real
                    // errors; reporting "Your resume was uploaded successfully"
                    // as a problem held up an application with nothing wrong.
                    const ok = /\\b(success|successfully|uploaded|saved|complete[d]?)\\b/i;
                    const bad = /\\b(error|invalid|required|must|cannot|failed|unable|select an option)\\b/i;
                    // Next.js announces each route change in a clipped,
                    // one-pixel role="alert" for screen readers. It holds the
                    // page title, and was reported as a form error.
                    const announcement = e => {
                        const box = e.getBoundingClientRect();
                        const style = getComputedStyle(e);
                        return box.height <= 1 || box.width <= 1
                            || (style.clip || '').startsWith('rect(0')
                            || e.id === '__next-route-announcer__';
                    };
                    const errors = [...document.querySelectorAll('[role=alert], [aria-invalid=true], [class*=error i]')]
                        .filter(e => visible(e) && !announcement(e))
                        .map(e => (e.innerText || '').trim())
                        .filter(t => t && t.length < 200 && (bad.test(t) || !ok.test(t)));
                    return {required_still_blank: [...new Set(blanks)], errors_shown: [...new Set(errors)]};
                }"""
            )
        except Exception as exc:
            logger.warning("Required-field check failed: %s", exc)
            return {"required_still_blank": [], "errors_shown": []}

    def upload_via_chooser(self, page: Page, label_pattern: str, file_path: Path) -> bool:
        """Uploads through a clickable tile ("Upload a Resume", "Attach a Cover
        Letter") that opens the system file picker instead of exposing a file
        input. The picker is answered programmatically with the file."""
        # Platform-specific upload first (SuccessFactors' "+" icon, say).
        kind = "cover_letter" if "cover" in label_pattern.lower() else "resume"
        if self.adapter(page).upload_attachment(self, page, kind, Path(file_path)):
            return True

        tile = page.locator("a, button, [role=button], div, span").filter(
            has_text=re.compile(label_pattern, re.IGNORECASE)
        )
        for i in range(min(tile.count(), 8) - 1, -1, -1):  # innermost matches come last
            el = tile.nth(i)
            try:
                if not el.is_visible() or len((el.inner_text() or "")) > 60:
                    continue
                try:
                    with page.expect_file_chooser(timeout=5_000) as chooser:
                        self._click_resiliently(el, timeout_ms=4_000)
                    chooser.value.set_files(str(file_path))
                except Exception:
                    # The tile opened an upload dialog instead of the picker
                    # (SuccessFactors: "Upload a Resume -- Opens a dialog").
                    if not self._upload_in_dialog(page, file_path):
                        continue
                page.wait_for_timeout(4_000)
                # Some sites confirm in a small dialog after picking the file.
                confirm = page.get_by_role("button", name=re.compile(r"^\s*(upload|save|done|ok|attach)\s*$", re.IGNORECASE))
                for j in range(confirm.count()):
                    if confirm.nth(j).is_visible() and self._in_popup(confirm.nth(j)):
                        self._click_resiliently(confirm.nth(j), timeout_ms=4_000)
                        page.wait_for_timeout(3_000)
                        break
                logger.info("Uploaded %s through the %r tile", Path(file_path).name, label_pattern)
                return True
            except Exception:
                continue
        return False

    def upload_in_dialog(self, page: Page, file_path: Path) -> bool:
        """Finishes an upload inside a dialog: its file input if it has one,
        otherwise its Browse / "from my computer" button's file picker.

        Only ever works inside a dialog. Falling back to the whole page would
        put an application's own Submit button within reach of the confirm
        click below."""
        page.wait_for_timeout(2_000)
        overlay = self.adapter(page).overlay_selector()
        dialogs = page.locator(
            "[role=dialog], [aria-modal=true], [class*=dialog i], [class*=modal i]" + (f", {overlay}" if overlay else "")
        )
        dialog = None
        for i in range(dialogs.count() - 1, -1, -1):
            if dialogs.nth(i).is_visible() and dialogs.nth(i).locator("input[type=file], button, a").count():
                dialog = dialogs.nth(i)
                break
        if dialog is None:
            logger.info("UPLOAD_DIALOG: no upload dialog opened")
            return False
        root = dialog
        inputs = root.locator("input[type=file]")
        if inputs.count():
            inputs.last.set_input_files(str(file_path))
        else:
            browse = root.locator("button, a, [role=button], label").filter(
                has_text=re.compile(r"browse|choose|select (a )?file|upload (from|a file)|my (computer|device)|from (computer|device)|local", re.IGNORECASE))
            browse = next((browse.nth(i) for i in range(browse.count()) if browse.nth(i).is_visible()), None)
            if browse is None:
                btns = root.locator("button, a, [role=button]")
                labels = [btns.nth(i).inner_text().strip() for i in range(min(btns.count(), 200))
                          if btns.nth(i).is_visible() and btns.nth(i).inner_text().strip()][-12:]
                logger.warning("UPLOAD_DIALOG: no file box or Browse button (buttons: %s)", labels)
                return False
            with page.expect_file_chooser(timeout=8_000) as chooser:
                self._click_resiliently(browse, timeout_ms=4_000)
            chooser.value.set_files(str(file_path))
        page.wait_for_timeout(3_000)
        # "submit" is deliberately absent: on some forms that is the
        # application's own button.
        confirm = root.get_by_role("button", name=re.compile(r"^\s*(upload|save|done|ok|attach)\s*$", re.IGNORECASE))
        for i in range(confirm.count()):
            if confirm.nth(i).is_visible() and not safety.is_submit_label(confirm.nth(i).inner_text()):
                self._click_resiliently(confirm.nth(i), timeout_ms=5_000)
                page.wait_for_timeout(4_000)
                break
        return True

    def upload_resume(self, page: Page, resume_path: Path, file_input_selector: str = "input[type='file']") -> bool:
        """Ensures exactly one resume is attached, and that it's the right
        file. Idempotent: if the only attachment already has the target
        filename it does nothing, so this can run on every loop pass
        without stacking up duplicates (which is what happened before).

        An override path can be supplied out-of-band via
        data/_resume_override.txt, so a JD-tailored resume can replace the
        generic one without restarting this process."""
        target = Path(resume_path)
        override_file = Path("data/_resume_override.txt")
        try:
            if override_file.exists():
                override = override_file.read_text(encoding="utf-8").strip()
                if override:
                    target = Path(override)
        except Exception as exc:
            logger.warning("Could not read resume override: %s", exc)

        if not target.exists():
            logger.warning("Resume file not found: %s", target)
            return False

        delete_buttons = page.locator("button[data-automation-id='delete-file']")
        try:
            attached = delete_buttons.count()
            labels = [
                (delete_buttons.nth(i).get_attribute("aria-label") or "") for i in range(attached)
            ]
        except Exception:
            attached, labels = 0, []

        self.attached_resume = target  # what the rest of the run should expect
        if attached == 1 and target.name in labels[0]:
            return True  # already exactly the file we want

        for _ in range(attached):
            try:
                page.locator("button[data-automation-id='delete-file']").first.click(timeout=5_000)
                page.wait_for_timeout(800)
            except Exception as exc:
                logger.warning("Could not delete an existing attachment: %s", exc)
                break

        # Non-Workday forms have no delete-file buttons, so attached==0 there
        # even when the file is already on the form (e.g. put there by the
        # site's own resume autofill). Re-uploading on every loop pass re-runs
        # that autofill, which can overwrite corrected fields.
        if attached == 0 and self._file_already_attached(page, target.name):
            return True
        if self.adapter(page).attachment_is_empty(page, "resume") is False:
            return True  # this platform already holds a resume

        file_input = page.query_selector(file_input_selector)
        if not file_input:
            if self.upload_via_chooser(page, r"^\s*(upload|attach|add) (a |your )?(resume|cv)\s*$", target):
                return True
            logger.warning("No file upload input found on page")
            return False
        file_input.set_input_files(str(target))
        page.wait_for_timeout(1_500)
        logger.info("Uploaded resume: %s (replaced %d existing)", target.name, attached)
        # Some sites (Eightfold) pop a privacy agreement over the form as soon
        # as a resume is uploaded.
        self.accept_consent_dialog(page)
        return True

    def find_apply_control(self, page: Page):
        """The control that opens the application form, whatever gives it its
        name.

        Google's careers page labels its Apply link with aria-label and leaves
        the element's text empty, so matching on text alone found nothing: the
        agent decided it was already on the form, filled nothing, and reported
        an application it had never opened. Matching the accessible name (which
        covers aria-label), then the href, finds it.
        """
        candidates = [
            page.get_by_role("link", name=re.compile(r"^\s*apply\b", re.I)),
            page.get_by_role("button", name=re.compile(r"^\s*apply\b", re.I)),
            page.locator("a[href*='/apply'], a[href^='./apply'], a[href*='apply?']"),
            page.locator("a:has-text('Apply'), button:has-text('Apply')"),
        ]
        for candidate in candidates:
            try:
                for i in range(min(candidate.count(), 5)):
                    element = candidate.nth(i)
                    if not element.is_visible():
                        continue
                    name = (element.get_attribute("aria-label") or element.inner_text() or "").strip()
                    logger.info("Apply control found: %r", name[:40] or "(unnamed link)")
                    element.scroll_into_view_if_needed(timeout=3_000)
                    return element.element_handle(timeout=3_000)
            except Exception as exc:
                logger.debug("Apply lookup failed: %s", str(exc).splitlines()[0][:100])
        return None

    def apply_destination(self, page: Page) -> str:
        """The address an unlabelled Apply link points at, absolute.

        Google's posting renders the Apply button's label in script that had
        not run when the agent looked, so the element carried no text and no
        aria-label -- only href="./apply?jobId=...". Matching on the address
        finds it whatever the label does.
        """
        try:
            href = page.evaluate("""() => {
                const links = [...document.querySelectorAll('a[href]')]
                    .filter(a => /(^|\\/|\\.)apply(\\?|$|\\/)/i.test(a.getAttribute('href') || ''));
                return links.length ? links[0].href : '';
            }""")
        except Exception as exc:
            logger.debug("Apply address lookup failed: %s", str(exc).splitlines()[0][:100])
            return ""
        return href or ""

    def click_apply_button(self, page: Page) -> Page:
        """Many ATS postings (Workday, Greenhouse...) show a JD page with an
        'Apply' link/button that leads to the actual form -- sometimes in a
        new tab. This just navigates there; it submits nothing. Returns the
        page to keep working with (same page, or the new tab if one opened)."""
        btn = self.find_apply_control(page)
        if btn is None:
            destination = self.apply_destination(page)
            if destination:
                # The control carries no label yet (Google renders it after the
                # page settles), but its link is in the page. Following it is
                # what clicking it would do.
                logger.info("Apply link found by address: %s", destination[:90])
                page.goto(destination, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(2_000)
                return page
            logger.info("No 'Apply' button found -- assuming already on the application form")
            return page

        context = page.context
        try:
            with context.expect_page(timeout=5_000) as new_page_info:
                btn.click()
            target = new_page_info.value
            logger.info("'Apply' opened a new tab: %s", target.url)
        except Exception:
            target = page
            logger.info("'Apply' navigated in place")

        try:
            target.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:
            pass
        try:
            target.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            target.wait_for_timeout(3_000)
        return target

    def dismiss_apply_chooser(self, page: Page) -> Page:
        """Some postings show a 'Start Your Application' chooser (Autofill
        with Resume / Apply Manually / Use My Last Application / Apply With
        LinkedIn) before the real form. Always choose 'Apply Manually' --
        the plain path our own field detector/filler can handle, and it
        never touches the LinkedIn OAuth button."""
        # In order of preference. "Without an account" comes first: it needs no
        # credentials and creates nothing, where signing in or creating an
        # account would. None of these is an identity provider's button --
        # "Continue with LinkedIn" and its like are never chosen.
        wordings = (
            "Apply without an Account", "Apply Without An Account",
            "Continue without an account", "Apply as a Guest", "Continue as Guest",
            "Apply Manually",
        )
        btn = None
        for wording in wordings:
            for selector in (f"button:has-text({wording!r})", f"a:has-text({wording!r})"):
                found = page.query_selector(selector)
                if found and found.is_visible():
                    btn = found
                    break
            if btn:
                logger.info("Apply chooser detected; selecting %r", wording)
                break
        if not btn:
            return page
        btn.click()
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            page.wait_for_timeout(3_000)
        return page

    @staticmethod
    def _click_resiliently(locator, timeout_ms: int = 8_000) -> bool:
        """Clicks through the actionability failures these Workday pages
        keep producing. A normal click waits for the element to be the
        topmost thing at its coordinates, which fails when an overlay or
        decorative element sits on top; force=True skips that check, and
        dispatch_event fires the handler directly as a last resort."""
        for attempt, action in enumerate(
            (
                lambda: locator.click(timeout=timeout_ms),
                lambda: locator.click(force=True, timeout=timeout_ms),
                lambda: locator.dispatch_event("click"),
            ),
            start=1,
        ):
            try:
                action()
                return True
            except Exception as exc:
                logger.warning("Click strategy %d failed: %s", attempt, str(exc)[:120])
        return False

    @staticmethod
    def _read_ats_password() -> str:
        """Reads ATS_PASSWORD fresh from .env at call time rather than
        using the value loaded at process start, so a password can be
        supplied without restarting (and losing the browser session)."""
        try:
            for line in Path(".env").read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("ATS_PASSWORD="):
                    return line.split("=", 1)[1].strip()
        except Exception as exc:
            logger.warning("Could not read ATS_PASSWORD: %s", exc)
        return ""

    # ------------------------------------------------------------------
    # Employer accounts: sign in only where one exists, otherwise create it
    # ------------------------------------------------------------------
    def account_on_record(self) -> Optional[bool]:
        """True/False whether the database records an account with this
        employer; None when that can't be known (no tracker, no employer)."""
        tracker, employer = getattr(self, "tracker", None), (getattr(self, "employer", "") or "").strip()
        if tracker is None or not employer or not hasattr(tracker, "get_ats_account"):
            return None
        try:
            return tracker.get_ats_account(employer) is not None
        except Exception as exc:
            logger.warning("Could not check the account record: %s", exc)
            return None

    def remember_account(self, page: Page, email: str, method: str) -> None:
        tracker, employer = getattr(self, "tracker", None), (getattr(self, "employer", "") or "").strip()
        if tracker is not None and employer and hasattr(tracker, "record_ats_account"):
            try:
                tracker.record_ats_account(employer, email, urlparse(page.url).netloc.lower(), method)
            except Exception as exc:
                logger.warning("Could not record the account: %s", exc)

    @staticmethod
    def _create_account_control(root):
        pattern = re.compile(
            r"^\s*(create (an |a new |your |my )?account|register( now)?|sign up|new user\??)\s*$", re.IGNORECASE
        )
        for role in ("link", "button"):
            loc = root.get_by_role(role, name=pattern)
            for i in range(min(loc.count(), 5)):
                try:
                    if loc.nth(i).is_visible():
                        return loc.nth(i)
                except Exception:
                    continue
        return None

    def create_account_from_link(self, page: Page, email: str) -> bool:
        control = self._create_account_control(page)
        if control is None:
            return False
        employer = getattr(self, "employer", "") or urlparse(page.url).netloc
        logger.info("ACCOUNT: no %s account on record -- creating one instead of trying to sign in", employer)
        # A cookie banner over the link swallowed the click on IGT's page.
        self.dismiss_cookie_banner(page)
        control = self._create_account_control(page) or control
        try:
            control.scroll_into_view_if_needed(timeout=3_000)
            control.click(timeout=8_000)
        except Exception:
            if not self._click_resiliently(control, timeout_ms=5_000):
                logger.warning("ACCOUNT_CREATE_FAILED: couldn't click the Create an account link")
                return False
        try:
            page.wait_for_function(
                "() => [...document.querySelectorAll('input[type=password]')].filter(e => e.getClientRects().length).length >= 2",
                timeout=20_000,
            )
        except Exception:
            logger.warning("ACCOUNT_CREATE_FAILED: the Create Account form didn't appear (page: %s)", page.url[:100])
            return False
        return self.fill_create_account_form(page, email)

    def fill_create_account_form(self, page: Page, email: str) -> bool:
        """Fills a careers-site Create Account form: email (and its retype),
        the .env password (and its retype), first/last name, country, and only
        the agreement checkboxes the form requires. Marketing opt-ins ("hear
        more about career opportunities") are left unticked. Records the
        account when the site accepts it."""
        password = self._read_ats_password()
        if not password:
            logger.info("No ATS_PASSWORD set; leaving account creation to the user")
            return False
        if not safety.password_allowed(page.url):
            logger.warning("ACCOUNT_HELD: %s is not a site the agent creates accounts on", urlparse(page.url).netloc)
            return False
        if safety.captcha_visible(page):
            logger.warning("ACCOUNT_HELD: a CAPTCHA is on the account form -- only you can complete it")
            return False
        visible = lambda loc: [loc.nth(i) for i in range(loc.count()) if loc.nth(i).is_visible()]
        pw_fields = visible(page.locator("input[type='password']"))
        if len(pw_fields) < 2:
            return False  # not a Create Account form
        self._create_form_attempted = True
        profile = getattr(self, "_profile", None)
        full_name = (getattr(profile, "full_name", "") or "").split()
        try:
            email_fields = visible(page.locator(
                "input[type='email'], input[name*='email' i], input[id*='email' i], "
                "input[name*='username' i], input[id*='username' i]"
            ))
            for box in email_fields:
                if not (box.input_value() or "").strip():
                    box.fill(email)
                    box.press("Tab")
            for box in pw_fields:
                box.fill(password)
                box.press("Tab")

            if full_name:
                for field in self.detect_form_fields(page):
                    value = {"first_name": full_name[0], "last_name": full_name[-1]}.get(field.matched_profile_key or "")
                    if value and field.selector and not (page.locator(field.selector).first.input_value() or "").strip():
                        page.fill(field.selector, value)
                        page.press(field.selector, "Tab")
            if profile is not None:
                self.answer_standard_questions(page, profile)  # country of residence

            self.adapter(page).create_account_extras(self, page)

            agree = self._profile is None or getattr(self._profile, "accept_application_privacy_prompts", True)

            # Terms that must be OPENED to be accepted ("Read and accept the
            # data privacy statement." -- IGT refused the form until it was).
            if agree:
                # IGT's is an <a role="button" aria-haspopup="dialog">, not a link.
                terms_text = re.compile(r"(read and )?accept.*(privacy|terms)|data privacy (statement|agreement)", re.IGNORECASE)
                terms = page.locator("a, button, [role=button], [role=link]").filter(has_text=terms_text)
                terms = next((terms.nth(i) for i in range(min(terms.count(), 5)) if terms.nth(i).is_visible()), None)
                if terms is not None:
                    pages_before = set(page.context.pages)
                    self._click_resiliently(terms, timeout_ms=5_000)
                    page.wait_for_timeout(3_000)
                    popups = [pg for pg in page.context.pages if pg not in pages_before]
                    target = popups[-1] if popups else page
                    # Some platforms draw it as a plain overlay div with no
                    # dialog role (see sites/successfactors.py), so ask the
                    # adapter for its container selector too.
                    extra = self.adapter(page).overlay_selector()
                    overlays = target.locator(
                        "[role=dialog], [aria-modal=true], [class*=overlay i], [class*=modal i]"
                        + (f", {extra}" if extra else "")
                    ).filter(has_text=re.compile(r"privacy|consent", re.IGNORECASE))
                    accepted = False
                    for i in range(overlays.count() - 1, -1, -1):
                        box = overlays.nth(i)
                        if not box.is_visible():
                            continue
                        btn = box.get_by_role("button", name=re.compile(r"^\s*(i )?(accept|agree)\s*$", re.IGNORECASE))
                        if btn.count() and btn.first.is_visible() and self._click_resiliently(btn.first, timeout_ms=5_000):
                            accepted = True
                            break
                    logger.info("ACCOUNT: data privacy statement %s", "accepted" if accepted else "opened, but no Accept button found")
                    page.wait_for_timeout(1_500)
            for box in visible(page.locator("input[type='checkbox']")):
                label = ""
                box_id = box.get_attribute("id")
                if box_id:
                    lab = page.locator(f"label[for={json.dumps(box_id)}]")
                    label = lab.first.inner_text() if lab.count() else ""
                label = (label or box.evaluate("e => e.closest('label')?.innerText || e.parentElement?.innerText || ''")).lower()
                if re.search(r"hear more|newsletter|campaign|marketing|job alert|promotion", label):
                    continue  # marketing opt-ins are the user's choice, not ours
                if safety.is_attestation(label):
                    logger.info("LEFT_FOR_YOU: account form asks you to certify %r", label.strip()[:70])
                    continue
                if agree and safety.is_privacy_consent(label) and not box.is_checked():
                    box.check(timeout=3_000)

            submit = page.get_by_role(
                "button", name=re.compile(r"^\s*(create (my )?account|register|sign up|submit)\s*$", re.IGNORECASE)
            )
            button = next((submit.nth(i) for i in range(submit.count()) if submit.nth(i).is_visible()), None)
            if button is None:
                button = page.locator("input[type='submit'][value*='Create' i]").first
                if not button.count():
                    logger.warning("ACCOUNT_CREATE_FAILED: no Create Account button found")
                    return False
            self._click_resiliently(button, timeout_ms=8_000)
            page.wait_for_timeout(5_000)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15_000)
            except Exception:
                pass

            still_on_form = len(visible(page.locator("input[type='password']"))) >= 2
            errors = [t.strip() for t in page.locator("[role=alert], [class*=error i]").all_inner_texts() if t.strip()]
            if still_on_form:
                logger.warning("ACCOUNT_CREATE_FAILED: the site kept the form open%s",
                               f" -- {' / '.join(errors)[:200]}" if errors else "")
                return False
            logger.info("ACCOUNT_CREATED: new %s account for %s",
                        getattr(self, "employer", "") or urlparse(page.url).netloc, email)
            self.remember_account(page, email, "password")
            return True
        except Exception as exc:
            logger.warning("ACCOUNT_CREATE_FAILED: %s", str(exc).splitlines()[0][:160])
            return False

    def create_ats_account(self, page: Page, email: str) -> bool:
        """Completes an employer ATS 'Create Account' form: fills both
        password fields, ticks the personal-information acknowledgment,
        and submits. Only ever runs on employer career sites -- the same
        BLOCKED_LOGIN_DOMAINS guard as attempt_auto_login applies, and the
        password comes from the user's own .env, never generated here."""
        domain = urlparse(page.url).netloc.lower()
        if not safety.password_allowed(page.url):
            raise BlockedLoginDomainError(f"Account creation is off-limits on {domain}")

        password = self._read_ats_password()
        if not password:
            logger.info("No ATS_PASSWORD set; leaving account creation to the user")
            return False

        pw_fields = page.locator("input[type='password']")
        if pw_fields.count() < 2:
            return False  # not a create-account form (sign-in has one field)

        try:
            email_input = page.locator(
                "input[type='email'], input[name*='email' i], input[id*='email' i]"
            ).first
            if email_input.count() > 0 and not (email_input.input_value() or "").strip():
                email_input.fill(email)
            pw_fields.nth(0).fill(password)
            pw_fields.nth(1).fill(password)

            # Acknowledgment checkbox (required on some tenants). Only privacy
            # consent -- an attestation or a marketing opt-in is never ticked.
            boxes = page.locator("input[type='checkbox']")
            for i in range(boxes.count()):
                box = boxes.nth(i)
                if not box.is_visible() or box.is_checked():
                    continue
                label = (box.evaluate("e => e.closest('label')?.innerText || e.parentElement?.innerText || ''") or "")
                if safety.is_attestation(label) or re.search(r"marketing|newsletter|job alert", label, re.IGNORECASE):
                    logger.info("LEFT_FOR_YOU: not ticking %r", " ".join(label.split())[:70])
                    continue
                box.check(timeout=3_000)

            btn = page.locator("button:has-text('Create Account')").first
            if btn.count() == 0:
                logger.warning("Create Account button not found")
                return False
            if not self._click_resiliently(btn):
                return False
            page.wait_for_timeout(3_000)
            logger.info("Submitted Create Account form for %s", domain)
            return True
        except Exception as exc:
            logger.warning("Account creation failed on %s: %s", domain, exc)
            return False

    def attempt_auto_login(self, page: Page, email: str, password: str, scope=None) -> bool:
        """Fills and submits a login form -- ONLY for domains not in
        BLOCKED_LOGIN_DOMAINS. Raises BlockedLoginDomainError otherwise.
        Returns False (no-op) if no credentials are configured or no
        password field is visible on the page (nothing to log into).

        `scope` restricts every lookup to one form. Pass it whenever a sign-in
        modal is open over another form, or the fields of the form underneath
        get filled instead."""
        domain = urlparse(page.url).netloc.lower()
        if not safety.password_allowed(page.url):
            raise BlockedLoginDomainError(
                f"Refusing to type a password on {domain} -- job-board and Google/Apple/Microsoft "
                "account logins are off-limits regardless of configured credentials."
            )
        if safety.captcha_visible(page):
            logger.warning("LOGIN_HELD: a CAPTCHA is on the sign-in page -- only you can complete it")
            return False
        password = password or self._read_ats_password()
        if not email or not password:
            logger.info("No ATS credentials configured; skipping auto-login")
            return False

        if scope is None:
            scope = self._sign_in_scope(page)
        root = scope if scope is not None else page

        pw_locator = root.locator("input[type='password']").first
        if pw_locator.count() == 0 or not pw_locator.is_visible():
            if self.sign_in_with_google_if_offered(page, email):
                return True
            logger.info("No visible password field on %s; assuming no login is required", domain)
            return False

        # Don't try saved credentials on an employer we have no account with:
        # IGT rejected them twice before anyone checked. Create the account
        # instead when the page offers to.
        if self.account_on_record() is False and self._create_account_control(page) is not None:
            return self.create_account_from_link(page, email)

        email_locator = self._find_login_email_input(root)
        if email_locator is None:
            # The second page of a two-step sign-in: iCIMS (login.icims.com)
            # asks for the email on one page and the password on the next, so
            # there is no email box here -- only the email shown as already
            # given. Without this the run stopped at a lone password box.
            if not self._email_already_given(page, email):
                logger.warning("Password field present but no email/username field found on %s", domain)
                return False
            tried = self.__dict__.setdefault("_password_steps_tried", set())
            if domain in tried:
                logger.warning("LOGIN_HELD: the password was already tried once on %s -- not retrying", domain)
                return False
            tried.add(domain)
            logger.info("Two-step sign-in on %s: the email was given on the page before; entering the password",
                        domain)
        else:
            email_locator.fill(email)
        pw_locator.fill(password)

        # Sign-in buttons are often duplicated (one hidden) or covered by a
        # cookie banner, so go through the same resilient click path that the
        # rest of the Workday flow uses instead of a bare .click().
        for selector in (
            "button[data-automation-id='signInSubmitButton']",
            "button:has-text('Sign In')",
            "button:has-text('Sign in')",
            "button:has-text('Log In')",
            "button:has-text('Log in')",
            "button[type='submit']",
        ):
            if root.locator(selector).count() and self._click_resiliently(root.locator(selector).first):
                page.wait_for_load_state("domcontentloaded", timeout=15000)
                page.wait_for_timeout(3000)
                logger.info("Attempted auto-login on %s", domain)
                return self._after_login_attempt(page, email)

        # Some ATS login forms submit on Enter even when no button matches.
        pw_locator.press("Enter")
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(3000)
        logger.info("Attempted auto-login on %s via Enter key", domain)
        return self._after_login_attempt(page, email)

    @staticmethod
    def _email_already_given(page: Page, email: str) -> bool:
        """True when the page shows this email as the account being signed in
        to -- as text ("jonna...@gmail.com  Edit") or in a read-only or hidden
        box -- which is how the password step of a two-step sign-in looks."""
        if not email:
            return False
        try:
            return bool(page.evaluate(
                """email => {
                    email = email.toLowerCase();
                    const boxes = [...document.querySelectorAll('input')].filter(i =>
                        i.type === 'hidden' || i.readOnly || i.disabled || !i.getClientRects().length);
                    return boxes.some(i => (i.value || '').trim().toLowerCase() === email)
                        || (document.body.innerText || '').toLowerCase().includes(email);
                }""", email))
        except Exception:
            return False

    def _after_login_attempt(self, page: Page, email: str) -> bool:
        """Records a successful sign-in; on a rejected one, stops retrying and
        creates the account if the page offers that."""
        body = ""
        try:
            body = (page.locator("body").inner_text(timeout=5_000) or "").lower()
        except Exception:
            pass
        rejected = re.search(r"invalid (email|user|login|password|credentials)|incorrect (email|password)|wrong (email|username|password)|"
                             r"don't recognize|not recognized|no account", body)
        pw_visible = page.locator("input[type='password']").first
        still_login = pw_visible.count() > 0 and pw_visible.is_visible()
        if not rejected and not still_login:
            logger.info("LOGIN_OK: signed in as %s", email)
            self.remember_account(page, email, "password")
            return True
        if rejected:
            logger.warning("LOGIN_REJECTED: the site didn't accept %s -- not retrying", email)
            if self.account_on_record() is not True and self._create_account_control(page) is not None:
                return self.create_account_from_link(page, email)
        return False

    @staticmethod
    def _find_login_email_input(root):
        """Locates the username/email box on a login form, returning a
        Locator. Workday renders it as a plain input[type=text] with a
        data-automation-id and no name/id containing 'email', so attribute
        guesses alone miss it; the last resort is 'the visible text input
        just above the password field', which is what a human reads it as.

        `root` is a Page or a form Locator -- both expose .locator()."""
        for selector in (
            "input[data-automation-id='email']",
            "input[data-automation-id*='email' i]",
            "input[data-automation-id*='userName' i]",
            "input[type='email']",
            "input[name*='email' i]",
            "input[name*='username' i]",
            "input[id*='email' i]",
            "input[id*='username' i]",
            "input[aria-label*='email' i]",
            "input[autocomplete='username']",
        ):
            # Not a read-only box: on the password step of a two-step sign-in
            # it only shows the email already given, and can't be typed in.
            loc = root.locator(f"{selector}:not([readonly]):not([disabled])").first
            try:
                if loc.count() and loc.is_visible():
                    return loc
            except Exception:
                continue

        # Walk the inputs in DOM order and take the last plain text box that
        # sits ABOVE the password field -- taking simply "the last text input
        # here" would grab the header job-search box instead.
        inputs = root.locator("input")
        preceding = None
        for i in range(min(inputs.count(), 40)):
            el = inputs.nth(i)
            try:
                if not el.is_visible() or not el.is_editable():
                    continue
                itype = (el.get_attribute("type") or "text").lower()
            except Exception:
                continue
            if itype == "password":
                return preceding
            if itype in ("text", "email", "tel", ""):
                preceding = el
        return None

    def detect_screening_questions(
        self, page: Page, known_fields: list[DetectedField]
    ) -> list[DetectedQuestion]:
        """Finds form elements NOT already matched to a known profile field --
        almost always employer-specific screening/technical questions
        (e.g. 'Describe your BGP experience', 'Are you authorized to work
        in the US?').

        Fields belonging to repeated Work Experience/Education entries are
        excluded: every one of them is labelled 'Role Description*', so they
        look like four copies of one question and all four got answered with
        the same (first) role's text, overwriting the other roles."""
        known_selectors = {f.selector for f in known_fields if f.matched_profile_key}
        questions: list[DetectedQuestion] = []

        for el in page.query_selector_all("textarea"):
            if not el.is_visible() or self._is_structured_entry_field(el):
                continue
            selector = self._build_selector(el)
            if not selector or selector in known_selectors:
                continue
            questions.append(DetectedQuestion(
                question_text=self._label_for(page, el) or "(unlabeled text question)",
                input_type="textarea", selector=selector,
            ))

        for el in page.query_selector_all("select"):
            if not el.is_visible() or self._is_structured_entry_field(el):
                continue
            selector = self._build_selector(el)
            if not selector or selector in known_selectors:
                continue
            options = [o.inner_text().strip() for o in el.query_selector_all("option") if o.inner_text().strip()]
            questions.append(DetectedQuestion(
                question_text=self._label_for(page, el) or "(unlabeled dropdown)",
                input_type="select", selector=selector, options=options,
            ))

        # Grouped as the page groups them: Google gives every radio the same
        # name and separates the questions with role="radiogroup", so keying on
        # the name made five questions look like one with thirteen answers --
        # and an answer could have been ticked on the wrong question.
        for group in self.radio_groups(page):
            if group["checked"] or not group["question"]:
                continue
            questions.append(DetectedQuestion(
                question_text=group["question"], input_type="radio",
                selector=group["key"], options=group["labels"],
            ))

        # Questions rendered in a shape this scan cannot see (Amazon drives a
        # hidden select with no id through a span), asked of the adapter.
        for platform_question in self._adapter_hook(page, "platform_questions", [], page):
            text = platform_question.get("question") or ""
            if not text or any(q.question_text == text for q in questions):
                continue
            # Already answered -- by the profile, the site or the user. Asking
            # Claude again let a drafted "Yes" overwrite the profile's "No" on
            # Amazon's export-control declaration.
            if self._has_answer(platform_question.get("value")):
                continue
            questions.append(DetectedQuestion(
                question_text=text, input_type="platform",
                selector=platform_question.get("qid", ""),
                options=list(platform_question.get("options") or []),
            ))

        logger.info("Detected %d screening question(s)", len(questions))
        return questions

    def fill_screening_answers(
        self, page: Page, questions: list[DetectedQuestion], answers: dict[str, str]
    ) -> int:
        """answers is keyed by DetectedQuestion.question_text. Only fills
        questions we have a drafted answer for -- never guesses blindly."""
        filled = 0
        for q in questions:
            answer = answers.get(q.question_text)
            if not answer:
                continue
            try:
                if q.input_type == "platform":
                    if self._adapter_hook(page, "answer_platform_question", False,
                                          self, page, q.selector, answer):
                        self.values.record(page, f"[data-questionid={json.dumps(q.selector)}]",
                                           answer, "screening answer")
                        logger.info("PLATFORM_ANSWER: %r -> %r", q.question_text[:60], answer[:40])
                        filled += 1
                elif q.input_type == "textarea":
                    page.fill(q.selector, answer)
                    filled += 1
                elif q.input_type == "select":
                    if self._select_option_safely(page, q.selector, answer, q.question_text):
                        filled += 1
                elif q.input_type == "radio":
                    group = next((g for g in self.radio_groups(page)
                                  if g["key"] == q.selector or g["question"] == q.question_text), None)
                    if group and self.answer_radio_group(page, group, answer):
                        index = self._best_option(group["labels"], [answer])
                        self.values.record(page, f"[id={json.dumps(group['ids'][index])}]",
                                           answer, "screening answer")
                        filled += 1
            except Exception as exc:
                logger.warning("Could not fill screening answer for %r: %s", q.question_text, exc)
        logger.info("Filled %d/%d screening question answers", filled, len(questions))
        return filled

    def _select_option_safely(self, page: Page, selector: str, answer: str, question_text: str) -> bool:
        """Sets a native <select> without page.select_option()'s default 30s
        actionability wait when the drafted answer isn't a real option.

        Claude drafts the answer text before the form's actual option wording
        is known, so an exact match often isn't there. select_option() then
        retries internally for a FULL 30 SECONDS per field -- with 11
        screening questions on one Lever form, several of them selects with no
        exact match, that stalled the whole run for minutes. Options are read
        first and matched (exact, then semantically) with a short timeout, the
        same equivalence-gated approach already used for the other dropdown
        types, so a bad match is refused rather than guessed."""
        try:
            select = page.locator(selector).first
            options = [o.strip() for o in select.locator("option").all_inner_texts() if o.strip()]
            current = (select.input_value() or "").strip()
        except Exception as exc:
            logger.warning("Could not read options for %r: %s", question_text, exc)
            return False
        if not options:
            return False

        # These no-JS Lever-style selects use their FIRST option's text as an
        # instructional placeholder ('Select...', 'Yes or No', 'A or B or C')
        # rather than a real blank value -- so "current != options[0]" is the
        # general test for "already answered". Skipping in that case matters:
        # this method also runs from the generic screening-question pass,
        # which runs AFTER the label-targeted answers above and would
        # otherwise overwrite a correct answer with Claude's guess (drafted
        # against '(unlabeled dropdown)' with no real question text, so it's
        # one generic answer reused across all of them).
        if current and options and current != options[0]:
            logger.info("%r already answered with %r; leaving it alone", question_text, current)
            return True

        for opt in options:
            if opt.strip().lower() == answer.strip().lower():
                try:
                    select.select_option(label=opt, timeout=3_000)
                    return True
                except Exception as exc:
                    logger.warning("Exact option %r for %r did not apply: %s", opt, question_text, exc)
                    return False

        pick = self._match_option_semantically([answer], options, question_text)
        if pick:
            try:
                select.select_option(label=pick, timeout=3_000)
                logger.info("Answered %r with %r (semantic)", question_text, pick)
                return True
            except Exception as exc:
                logger.warning("Semantic option %r for %r did not apply: %s", pick, question_text, exc)
                return False

        logger.warning(
            "No option matched drafted answer %r for %r. Available: %s",
            answer, question_text, options[:15],
        )
        return False

    def _find_first_matching(self, page: Page, hints: list[str], only_if_empty: bool = True):
        """Returns the first visible element handle whose label matches one
        of the given hints (and is currently empty, if only_if_empty), or
        None. Used for repeatable sections (multiple Work Experience/
        Education entries) where the same label text repeats per entry:
        the first still-empty match naturally targets the newest entry,
        without needing to know the site's internal container structure."""
        for el in page.query_selector_all("input[type='text'], input:not([type]), textarea, select"):
            if not el.is_visible():
                continue
            label = self._label_for(page, el).lower()
            if not any(hint in label for hint in hints):
                continue
            if only_if_empty:
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                if tag != "select":
                    try:
                        if el.input_value().strip():
                            continue
                    except Exception:
                        pass
            return el
        return None

    def fill_first_matching(
        self, page: Page, hints: list[str], value: str, only_if_empty: bool = True
    ) -> bool:
        """Fills the first matching empty field. See _find_first_matching."""
        if not value:
            return False
        el = self._find_first_matching(page, hints, only_if_empty=only_if_empty)
        if not el:
            return False
        try:
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            if tag == "select":
                el.select_option(label=value)
            else:
                el.fill(value)
            return True
        except Exception as exc:
            logger.warning("Could not fill field matching %s: %s", hints, exc)
            return False

    def fill_first_matching_verified(
        self, page: Page, hints: list[str], value: str,
        only_if_empty: bool = True, settle_ms: int = 1000, retries: int = 2,
    ) -> bool:
        """Like fill_first_matching, but holds the SAME element handle and
        re-fills it if the value doesn't stick after a short settle window.
        Some sites asynchronously overwrite a field shortly after it's
        set (e.g. their own resume auto-parse firing late) -- this makes
        sure OUR value is the one still there afterward, not theirs."""
        if not value:
            return False
        el = self._find_first_matching(page, hints, only_if_empty=only_if_empty)
        if not el:
            return False
        for attempt in range(retries + 1):
            try:
                if attempt == 0:
                    el.fill(value)
                else:
                    # Some masked inputs (e.g. date pickers) ignore a
                    # directly-set value and only respond to real keystrokes.
                    el.click()
                    el.fill("")
                    el.type(value, delay=40)
            except Exception as exc:
                logger.warning("Could not fill field matching %s (attempt %d): %s", hints, attempt + 1, exc)
                continue
            page.wait_for_timeout(settle_ms)
            try:
                current = el.input_value()
            except Exception:
                current = None
            if current is not None and current.strip() == value.strip():
                return True
            logger.warning(
                "Value for %s changed after settling (attempt %d, now %r) -- retrying",
                hints, attempt + 1, (current or "")[:60],
            )
        return False

    def select_radio(self, page: Page, element) -> bool:
        """Selects one radio or checkbox, whatever the page puts in the way.

        The real input is usually hidden under the styling: Bootstrap covers it
        with its label, and Google's Material checkboxes ignore a click on the
        input itself ("clicking the checkbox did not change its state"). So the
        label is tried, then the wrapper a person actually clicks, and finally
        the input is set directly and told the page about it.
        """
        def is_set() -> bool:
            try:
                return bool(element.evaluate("e => e.checked"))
            except Exception:
                return False

        try:
            element.check(timeout=2_000)
            if is_set():
                return True
        except Exception:
            pass

        try:
            element_id = element.get_attribute("id") or ""
            if element_id:
                label = page.locator(f"label[for={json.dumps(element_id)}]").first
                if label.count():
                    label.click(timeout=3_000)
                    if is_set():
                        return True
        except Exception:
            pass

        # The wrapper that carries the styling is what a person clicks.
        try:
            element.evaluate("""e => {
                const target = e.closest('label, [role=checkbox], [role=radio], [class*=checkbox], [class*=radio]')
                            || e.parentElement;
                if (target) { target.scrollIntoView({block: 'center'}); target.click(); }
            }""")
            page.wait_for_timeout(300)
            if is_set():
                return True
        except Exception:
            pass

        # Last resort: set it and tell the page, the way the widget would.
        try:
            element.evaluate("""e => {
                e.checked = true;
                e.dispatchEvent(new Event('input', {bubbles: true}));
                e.dispatchEvent(new Event('change', {bubbles: true}));
                e.dispatchEvent(new Event('click', {bubbles: true}));
            }""")
            page.wait_for_timeout(300)
            if is_set():
                return True
        except Exception as exc:
            logger.warning("Could not select an option: %s", str(exc).splitlines()[0][:100])
        return False

    def check_first_matching(self, page: Page, hints: list[str]) -> bool:
        for el in page.query_selector_all("input[type='checkbox']"):
            if not el.is_visible():
                continue
            if any(hint in self._label_for(page, el).lower() for hint in hints):
                try:
                    el.check()
                    return True
                except Exception as exc:
                    logger.warning("Could not check box matching %s: %s", hints, exc)
                    return False
        return False


    def fill_by_id_suffix(self, page, id_suffix, index, value, retries=2):
        """Workday-specific; implemented in sites/workday.py."""
        page_ref = page
        adapter = self.adapter(page_ref) if hasattr(page_ref, 'url') else self.adapter(self._page_hint())
        handler = getattr(adapter, 'fill_by_id_suffix', None)
        if handler is None:
            logger.info('No fill_by_id_suffix handler for this site')
            return None
        return handler(self, page, id_suffix, index, value, retries)


    def fill_date_spinner(self, page, section_key, date_key, index, month, year):
        """Workday-specific; implemented in sites/workday.py."""
        page_ref = page
        adapter = self.adapter(page_ref) if hasattr(page_ref, 'url') else self.adapter(self._page_hint())
        handler = getattr(adapter, 'fill_date_spinner', None)
        if handler is None:
            logger.info('No fill_date_spinner handler for this site')
            return None
        return handler(self, page, section_key, date_key, index, month, year)


    def fill_nth_matching(self, page, hints, index, value, settle_ms=0, retries=2):
        """Workday-specific; implemented in sites/workday.py."""
        page_ref = page
        adapter = self.adapter(page_ref) if hasattr(page_ref, 'url') else self.adapter(self._page_hint())
        handler = getattr(adapter, 'fill_nth_matching', None)
        if handler is None:
            logger.info('No fill_nth_matching handler for this site')
            return None
        return handler(self, page, hints, index, value, settle_ms, retries)


    def delete_all_entries(self, page, section_heading, next_heading, max_deletes=12):
        """Workday-specific; implemented in sites/workday.py."""
        page_ref = page
        adapter = self.adapter(page_ref) if hasattr(page_ref, 'url') else self.adapter(self._page_hint())
        handler = getattr(adapter, 'delete_all_entries', None)
        if handler is None:
            logger.info('No delete_all_entries handler for this site')
            return None
        return handler(self, page, section_heading, next_heading, max_deletes)



    def click_add_button(self, page, section_heading):
        """Workday-specific; implemented in sites/workday.py."""
        page_ref = page
        adapter = self.adapter(page_ref) if hasattr(page_ref, 'url') else self.adapter(self._page_hint())
        handler = getattr(adapter, 'click_add_button', None)
        if handler is None:
            logger.info('No click_add_button handler for this site')
            return None
        return handler(self, page, section_heading)




    def fill_experience_section(self, page, experiences):
        """Workday-specific; implemented in sites/workday.py."""
        page_ref = page
        adapter = self.adapter(page_ref) if hasattr(page_ref, 'url') else self.adapter(self._page_hint())
        handler = getattr(adapter, 'fill_experience_section', None)
        if handler is None:
            logger.info('No fill_experience_section handler for this site')
            return None
        return handler(self, page, experiences)


    def fill_education_section(self, page, education):
        """Workday-specific; implemented in sites/workday.py."""
        page_ref = page
        adapter = self.adapter(page_ref) if hasattr(page_ref, 'url') else self.adapter(self._page_hint())
        handler = getattr(adapter, 'fill_education_section', None)
        if handler is None:
            logger.info('No fill_education_section handler for this site')
            return None
        return handler(self, page, education)



    def select_from_button_dropdown(self, page, id_suffix, index, candidates, locator):
        """Workday-specific; implemented in sites/workday.py."""
        page_ref = page
        adapter = self.adapter(page_ref) if hasattr(page_ref, 'url') else self.adapter(self._page_hint())
        handler = getattr(adapter, 'select_from_button_dropdown', None)
        if handler is None:
            logger.info('No select_from_button_dropdown handler for this site')
            return None
        return handler(self, page, id_suffix, index, candidates, locator)


    def _match_option_semantically(
        self, candidates: list[str], available: list[str], question: str = ""
    ) -> str:
        """Asks Claude which of a dropdown's ACTUAL options means the same as
        the value we hold, and returns it only if the answer is equivalent
        rather than merely closest.

        Employers word the same choice differently ('Masters' vs 'Masters of
        Science', 'Company Career Site' vs 'Corporate Website'), and
        hardcoding each tenant's vocabulary meant a code edit per site. The
        options come from the live page, so this adapts on its own.

        The equivalence gate is the important half. Substring matching once
        put 'Masters of Arts' on this user's application for a Master of
        Science, and a yes/no 'willing to travel' answer has no honest
        mapping onto 'Up to 25% / Up to 50%'. A near-miss here is a false
        statement on a real application, so anything not equivalent is
        refused and left for a human."""
        if not candidates or not available:
            return ""
        intended = candidates[0]
        try:
            client = self._claude_client()
            if client is None:
                return ""
            result = client.choose_option(intended, available, question)
        except Exception as exc:
            logger.warning("Semantic option match failed for %r: %s", intended, exc)
            return ""

        choice, reason = result.get("choice", ""), result.get("reason", "")
        if not result.get("equivalent"):
            if choice or reason:
                logger.warning(
                    "Refusing %r for %r: not equivalent (%s) -- leaving for a human",
                    choice or "(no option)", intended, reason,
                )
            return ""
        logger.info("Semantic match: %r -> %r (%s)", intended, choice, reason)
        return choice

    @staticmethod
    def _visible_option_texts(page: Page, limit: int = 60) -> list[str]:
        """The option labels currently on screen, for semantic matching."""
        options = page.locator("[data-automation-id='promptOption']")
        if options.count() == 0:
            options = page.locator("[role='option']")
        texts = []
        for i in range(min(options.count(), limit)):
            try:
                text = (options.nth(i).inner_text() or "").strip()
            except Exception:
                continue
            if text and text not in texts:
                texts.append(text)
        return texts

    def _claude_client(self):
        """Lazily built so this module stays importable (and hot-reloadable)
        without an API key configured."""
        if getattr(self, "_claude", None) is None:
            try:
                from claude_integration import ClaudeClient
                self._claude = ClaudeClient(self._config)
            except Exception as exc:
                logger.warning("Claude client unavailable for option matching: %s", exc)
                self._claude = None
        return self._claude

    @staticmethod
    def _question_text_for(page: Page, element) -> str:
        """The visible question a control belongs to, for context when
        matching its options."""
        try:
            return (element.evaluate(
                "el => (el.closest('[data-automation-id]')?.parentElement"
                " || el.parentElement)?.innerText || ''"
            ) or "").strip()[:200]
        except Exception:
            return ""

    def select_from_searchable_input(self, page, id_suffix, candidates):
        """Workday-specific; implemented in sites/workday.py."""
        page_ref = page
        adapter = self.adapter(page_ref) if hasattr(page_ref, 'url') else self.adapter(self._page_hint())
        handler = getattr(adapter, 'select_from_searchable_input', None)
        if handler is None:
            logger.info('No select_from_searchable_input handler for this site')
            return None
        return handler(self, page, id_suffix, candidates)



    def select_one_option(self, page, id_suffix, candidates):
        """Workday-specific; implemented in sites/workday.py."""
        page_ref = page
        adapter = self.adapter(page_ref) if hasattr(page_ref, 'url') else self.adapter(self._page_hint())
        handler = getattr(adapter, 'select_one_option', None)
        if handler is None:
            logger.info('No select_one_option handler for this site')
            return None
        return handler(self, page, id_suffix, candidates)






    def _click_visible_suggestion(self, page: Page, field, typed: str) -> bool:
        """Clicks the autocomplete suggestion that starts with what was typed.

        Some widgets render their suggestions without role='option' or an
        aria-controls link back to the input, so neither the ARIA lookup nor
        ArrowDown+Enter reaches them -- but the suggestion is plainly visible
        and clickable ('Fairfax, Virginia, United States'). Matching on the
        leading text keeps this from hitting an unrelated open list."""
        term = typed.split(",")[0].strip()
        if len(term) < 2:
            return False
        try:
            pattern = re.compile(rf"^\s*{re.escape(term)}\b", re.IGNORECASE)
            matches = page.get_by_text(pattern)
            for i in range(min(matches.count(), 10)):
                el = matches.nth(i)
                try:
                    if not el.is_visible():
                        continue
                    # Never click the input itself or its own label.
                    tag = el.evaluate("e => e.tagName.toLowerCase()")
                    if tag in ("input", "textarea", "label"):
                        continue
                    text = (el.inner_text() or "").strip()
                    if not text or len(text) > 120:
                        continue
                    if self._click_resiliently(el, timeout_ms=4_000):
                        page.wait_for_timeout(800)
                        logger.info("Clicked suggestion %r", text[:60])
                        return True
                except Exception:
                    continue
        except Exception as exc:
            logger.warning("Suggestion click failed for %r: %s", typed, exc)
        return False




    def dump_page_shape(self, page: Page) -> None:
        """Diagnostic: frames, clickable things, and form inputs per frame.

        Page-level selectors don't reach into iframes, so a form embedded in
        one looks like an empty page -- this shows whether that's what's
        happening before any guessing starts."""
        try:
            logger.info("PAGE_SHAPE url=%s frames=%d", page.url, len(page.frames))
            for frame in page.frames:
                try:
                    inputs = frame.locator(
                        "input:not([type='hidden']), textarea, select"
                    ).count()
                    files = frame.locator("input[type='file']").count()
                    clickable = []
                    els = frame.locator("button, a[role='button'], a[href*='apply' i], [role='button']")
                    for i in range(min(els.count(), 25)):
                        try:
                            el = els.nth(i)
                            if not el.is_visible():
                                continue
                            text = (el.inner_text() or "").strip().replace("\n", " ")[:40]
                            if text:
                                clickable.append(text)
                        except Exception:
                            continue
                    logger.info(
                        "  frame name=%r url=%s inputs=%d file_inputs=%d clickable=%s",
                        frame.name, (frame.url or "")[:90], inputs, files, clickable[:12],
                    )
                    selects = frame.locator("select")
                    for i in range(min(selects.count(), 10)):
                        s = selects.nth(i)
                        try:
                            logger.info(
                                "    select[%d] id=%r name=%r value=%r options=%s",
                                i, s.get_attribute("id"), s.get_attribute("name"),
                                s.input_value(), s.locator("option").all_inner_texts()[:6],
                            )
                        except Exception:
                            continue
                    empties = frame.locator("input[type='text'], input:not([type])")
                    for i in range(min(empties.count(), 15)):
                        e = empties.nth(i)
                        try:
                            if not e.is_visible() or (e.input_value() or "").strip():
                                continue
                            logger.info(
                                "    empty input id=%r name=%r aria=%r placeholder=%r",
                                e.get_attribute("id"), e.get_attribute("name"),
                                e.get_attribute("aria-label"), e.get_attribute("placeholder"),
                            )
                        except Exception:
                            continue
                except Exception as exc:
                    logger.info("  frame %r: could not inspect (%s)", frame.name, exc)
        except Exception as exc:
            logger.warning("Page shape dump failed: %s", exc)

    def dump_widget_html(self, page: Page, id_suffix: str) -> None:
        """Diagnostic: logs the markup of the widget wrapping a given input,
        so its real interactive element (often a button or a listbox
        trigger, not the input itself) can be identified."""
        try:
            field = page.locator(f"input[id$='{id_suffix}'], button[id$='{id_suffix}']").first
            if field.count() == 0:
                logger.info("WIDGET_DUMP[%s]: not found", id_suffix)
                return
            info = field.evaluate(
                """el => {
                    const up = el.closest('[data-automation-id]')?.parentElement || el.parentElement;
                    const ctrl = [...(up ? up.querySelectorAll('input,button,[role]') : [])].map(n => ({
                        tag: n.tagName,
                        type: n.getAttribute('type') || '',
                        role: n.getAttribute('role') || '',
                        id: n.id || '',
                        aid: n.getAttribute('data-automation-id') || '',
                        aria: n.getAttribute('aria-label') || '',
                        text: (n.innerText || '').slice(0, 40),
                    }));
                    return {
                        self_disabled: el.disabled,
                        self_readonly: el.readOnly,
                        self_value: el.value,
                        html: (up ? up.outerHTML : '').slice(0, 2500),
                        controls: ctrl,
                    };
                }"""
            )
            logger.info("WIDGET_DUMP[%s] controls=%s", id_suffix, info.get("controls"))
            logger.info(
                "WIDGET_DUMP[%s] disabled=%s readonly=%s value=%r",
                id_suffix, info.get("self_disabled"), info.get("self_readonly"), info.get("self_value"),
            )
            logger.info("WIDGET_DUMP[%s] html=%s", id_suffix, info.get("html"))
        except Exception as exc:
            logger.warning("Widget dump failed for %s: %s", id_suffix, exc)

    def dump_controls(self, page: Page, keyword: str) -> None:
        """Diagnostic: lists visible buttons/comboboxes/selects whose id,
        automation id, or text mentions the keyword -- for finding custom
        dropdown widgets that aren't plain <select> elements."""
        found = []
        for el in page.query_selector_all("button, select, [role='combobox'], [role='listbox'], [role='button']"):
            try:
                if not el.is_visible():
                    continue
                attrs = {
                    "tag": el.evaluate("e => e.tagName"),
                    "id": el.get_attribute("id") or "",
                    "automationId": el.get_attribute("data-automation-id") or "",
                    "ariaLabel": el.get_attribute("aria-label") or "",
                    "text": (el.inner_text() or "")[:40],
                }
                blob = " ".join(str(v) for v in attrs.values()).lower()
                if keyword.lower() in blob:
                    found.append(attrs)
            except Exception:
                continue
        logger.info("CONTROL_DUMP[%s]: %d: %s", keyword, len(found), found)

    def dump_field_ids(self, page: Page, id_contains: str) -> None:
        """Diagnostic: lists every visible input/select/textarea whose id
        contains the given fragment, with its id and data-automation-id.
        Label-text matching is unreliable on these custom widgets, so this
        is how we find the stable selectors to target instead."""
        found = []
        for el in page.query_selector_all("input, select, textarea"):
            try:
                if not el.is_visible():
                    continue
                el_id = el.get_attribute("id") or ""
                if id_contains.lower() not in el_id.lower():
                    continue
                found.append({
                    "id": el_id,
                    "automationId": el.get_attribute("data-automation-id"),
                    "value": (el.get_attribute("value") or "")[:40],
                })
            except Exception:
                continue
        logger.info("FIELD_DUMP[%s]: %d fields: %s", id_contains, len(found), found)

    def inspect_date_field(self, page: Page, visible_text: str, index: int = 0) -> dict:
        """Diagnostic: finds the input immediately following visible text
        (e.g. 'From') on the page and dumps its HTML/attributes, so we can
        see what kind of widget it really is instead of guessing at why
        .fill()/.type() aren't sticking."""
        try:
            heading = page.get_by_text(visible_text, exact=False).nth(index)
            el = heading.locator("xpath=following::input[1]")
            if el.count() == 0:
                logger.info("DATE_FIELD_INSPECT: no input found following text %r (index %d)", visible_text, index)
                return {"error": "no following input found"}
        except Exception as exc:
            logger.info("DATE_FIELD_INSPECT: lookup failed for %r: %s", visible_text, exc)
            return {"error": str(exc)}
        info = el.evaluate(
            """e => ({
                tag: e.tagName,
                type: e.type || null,
                readonly: e.readOnly,
                disabled: e.disabled,
                value: e.value,
                placeholder: e.placeholder,
                maxLength: e.maxLength,
                pattern: e.pattern,
                className: e.className,
                ariaLabel: e.getAttribute('aria-label'),
                dataAutomationId: e.getAttribute('data-automation-id'),
                outerHTML: e.outerHTML.slice(0, 500),
                parentOuterHTML: e.parentElement ? e.parentElement.outerHTML.slice(0, 800) : null,
            })"""
        )
        logger.info("DATE_FIELD_INSPECT[%d]: %s", index, info)
        return info

    # A section of a form that is open for editing, with a save of its own.
    # Dayforce will not let the wizard advance while one is open: the agent
    # pressed Next, the page ignored it, and the run called that the last step.
    _SECTION_SAVE_SELECTORS = (
        "button:has-text('Update')",
        "button:has-text('Save')",
        "button:has-text('Done')",
        "button:has-text('Apply Changes')",
    )

    def commit_open_sections(self, page: Page) -> int:
        """Presses a section's own Save/Update/Done.

        These commit part of a form; none of them sends an application --
        safety.is_submit_label() is checked all the same, and anything that
        reads as a submit is left alone.
        """
        committed = 0
        for selector in self._SECTION_SAVE_SELECTORS:
            buttons = page.locator(selector)
            for i in range(min(buttons.count(), 4)):
                button = buttons.nth(i)
                try:
                    label = (button.inner_text() or "").strip()
                    if safety.is_submit_label(label) or not button.is_visible():
                        continue
                    if not button.is_enabled() or self._in_popup(button):
                        continue
                    button.click(timeout=4_000)
                    page.wait_for_timeout(1_200)
                    logger.info("Saved an open section with %r", label[:30])
                    committed += 1
                except Exception as exc:
                    logger.debug("Could not save a section: %s", str(exc).splitlines()[0][:100])
        return committed

    def click_next_step(self, page: Page) -> bool:
        """Advances a multi-step application wizard (Workday etc.) by one
        page -- e.g. 'My Information' -> 'My Experience'. This is just
        moving through the draft, not submitting anything; the real
        application is only ever submitted by the user themselves on the
        final Review step."""
        # An open section first: its Save/Update is what the page is waiting
        # for, and Next does nothing until it is pressed.
        self.commit_open_sections(page)
        btn = self._wizard_button(page)
        if btn is None:
            return False
        before = self._page_fingerprint(page)
        try:
            btn.click(timeout=10_000)
        except Exception as exc:
            # A forward button that never becomes clickable is a form that
            # cannot be advanced -- which is something to hand over, with the
            # browser still open and the part-filled application in it. Letting
            # the timeout escape closed the window and threw away a Google
            # application the agent had already filled two steps of.
            logger.warning("Could not advance the form: %s", str(exc).splitlines()[0][:120])
            self._stuck_on = before
            return False
        # Workday-style wizards swap content client-side without a real
        # navigation, so wait_for_load_state resolves instantly here and
        # detecting fields immediately would catch the OLD step's stale DOM.
        # Give the SPA re-render a fixed settle window first.
        page.wait_for_timeout(2_500)
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            page.wait_for_timeout(2_000)
        if self._page_fingerprint(page) == before:
            # The click was accepted and nothing happened. Some buttons in a
            # single-page form only act on a real key press, so try the way a
            # person using a keyboard would.
            try:
                btn.focus()
                page.keyboard.press("Enter")
                page.wait_for_timeout(2_500)
                try:
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:
                    pass
                if self._page_fingerprint(page) != before:
                    logger.info("The form moved on when the button was pressed with the keyboard")
            except Exception as exc:
                logger.debug("Keyboard press failed: %s", str(exc).splitlines()[0][:100])

        self._stuck_on = before if self._page_fingerprint(page) == before else None
        if self._stuck_on:
            # The page stayed put. Whatever it is waiting for, it usually says
            # so somewhere on screen -- worth reporting rather than calling
            # this the last step in silence.
            try:
                said = page.evaluate("""() => {
                    // Ant shows its complaints in a toast that fades, and in
                    // a live region -- neither is an [role=alert] on a field.
                    const toasts = [...document.querySelectorAll(
                        '.ant-message, .ant-notification, [aria-live], [class*=toast], [class*=message-notice]')]
                        .map(e => (e.innerText || '').replace(/\\s+/g, ' ').trim())
                        .filter(t => t && t.length < 200);
                    if (toasts.length) return toasts.slice(0, 4);
                    const visible = e => !!(e.offsetParent || e.getClientRects().length);
                    const box = e => e.getBoundingClientRect();
                    return [...document.querySelectorAll('[role=alert], [class*=error i], [class*=explain], [class*=warning i]')]
                        .filter(e => visible(e) && box(e).height > 1)
                        .map(e => (e.innerText || '').replace(/\\s+/g, ' ').trim())
                        .filter(t => t && t.length < 200).slice(0, 5);
                }""")
            except Exception:
                said = []
            logger.warning("The page did not move on%s",
                           (": " + "; ".join(said)) if said else " and said nothing about why")
        return True

    _CONFIRMATION_PHRASES = (
        "thank you for applying", "application submitted", "thanks for applying",
        "we have received your application", "your application has been submitted",
        "submission received", "application received",
        # Ashby / Lever wording
        "application was successfully submitted", "successfully submitted your application",
        "your application was submitted",
    )

    def submission_confirmed(self, page: Page, job_title: str = "") -> bool:
        """True when the human has submitted the application themselves:
        either the page shows an application-received confirmation with the
        Submit button gone, or the site's own applications list shows THIS
        job as applied. Eightfold sites (CBTS) show no thank-you message --
        they jump straight to 'My applications'."""
        if self.find_submit_button(page) is not None:
            return False  # still on the form
        body = (page.locator("body").inner_text(timeout=5_000) or "").lower()
        if any(phrase in body for phrase in self._CONFIRMATION_PHRASES):
            return True
        if not job_title:
            return False
        return bool(page.evaluate(
            """title => {
                const want = title.trim().toLowerCase();
                const hits = [...document.querySelectorAll('h1,h2,h3,h4,h5,a,span,div,p')]
                    .filter(e => e.children.length <= 2 && (e.innerText || '').trim().toLowerCase() === want
                                 && e.getClientRects().length);
                for (const hit of hits) {
                    let card = hit;
                    for (let i = 0; i < 5 && card.parentElement; i++) {
                        card = card.parentElement;
                        const text = (card.innerText || '').toLowerCase();
                        // Stop once this spans more than one job's card -- otherwise
                        // ANOTHER job's "Applied on" counts for this one.
                        const statuses = (text.match(/applied on|date applied|submitted on|draft|continue application/g) || []).length;
                        if (text.length > 600 || statuses > 2 || card.querySelectorAll('h1,h2,h3,h4,h5').length > 1) break;
                        // No word boundaries: inline labels run together ("draftcontinue application").
                        if (/draft|continue application|resume application|incomplete/.test(text)) break;
                        if (/applied on|date applied|submitted on|application submitted/.test(text)) return true;
                    }
                }
                return false;
            }""",
            job_title,
        ))

    _CONFIRMATION_MAIL = re.compile(
        r"thank(s| you) for (applying|your application|your interest)|application (received|submitted|confirmation)"
        r"|(we('ve| have)|has been) received|successfully (applied|submitted)|your application (for|to|has|was)",
        re.IGNORECASE,
    )

    def gmail_shows_confirmation(self, page: Page, company: str, job_title: str = "") -> Optional[str]:
        """Looks in the Gmail this browser is already signed in to for the
        employer's application-received email, in a separate tab so the
        application page is left alone. Read-only: it searches and reads the
        result rows, never opens, replies to or changes a message. Returns the
        matching row's subject/snippet, or None."""
        if not company:
            return None
        tab = page.context.new_page()
        try:
            query = quote(f"{company} newer_than:2d")
            tab.goto(f"https://mail.google.com/mail/u/0/#search/{query}", wait_until="domcontentloaded", timeout=45_000)
            try:
                tab.locator("tr.zA, td.TC").first.wait_for(state="attached", timeout=25_000)
            except Exception:
                pass
            tab.wait_for_timeout(2_000)
            if "accounts.google.com" in tab.url or "mail.google.com" not in tab.url:
                logger.warning("GMAIL: this browser isn't signed in to Gmail -- can't check for a confirmation email")
                return None

            rows = tab.locator("tr.zA")
            texts = [" ".join(t.split()) for t in rows.all_inner_texts()[:25]]
            if not texts:  # Gmail markup changed: fall back to the results pane's text
                main = tab.locator("[role=main]").first
                texts = [" ".join(t.split()) for t in (main.inner_text() if main.count() else "").split("\n") if t.strip()]
            wanted = [w.lower() for w in (company, job_title) if w]
            for text in texts:
                low = text.lower()
                if any(w in low for w in wanted) and self._CONFIRMATION_MAIL.search(text):
                    logger.info("GMAIL: confirmation email found -- %s", text[:160])
                    return text[:300]
            for text in texts[:5]:
                if any(w in text.lower() for w in wanted):
                    logger.info("GMAIL: %s email not counted as a confirmation: %s", company, text[:160])
            logger.info("GMAIL: no application-confirmation email from %s yet (%d result rows)", company, len(texts))
            return None
        except Exception as exc:
            logger.warning("GMAIL: check failed: %s", str(exc).splitlines()[0][:160])
            return None
        finally:
            try:
                tab.close()
                page.bring_to_front()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Emailed one-time passcodes (account setup / sign-in only)
    # ------------------------------------------------------------------
    _PASSCODE_FIELD = re.compile(r"passcode|one[- ]time (password|code)|verification code|security code|\botp\b", re.IGNORECASE)
    _CODE_MAIL = re.compile(r"passcode|one[- ]time|verification|verify|security code|\bcode\b", re.IGNORECASE)

    def _passcode_field(self, page: Page):
        """The passcode box on a 'we emailed you a code' page, or None."""
        try:
            body = page.locator("body").inner_text(timeout=5_000) or ""
            if not re.search(r"sent|emailed|check your (email|inbox)", body, re.IGNORECASE):
                return None
            for el in page.locator("input:not([type=hidden]):not([type=checkbox]):not([type=radio])").all()[:40]:
                if not el.is_visible():
                    continue
                label = el.evaluate(
                    "e => [e.getAttribute('aria-label'), e.placeholder, e.name, e.id,"
                    " e.id && document.querySelector(`label[for=\"${CSS.escape(e.id)}\"]`)?.innerText,"
                    " e.closest('tr, .form-group, div')?.innerText].filter(Boolean).join(' ')"
                ) or ""
                if self._PASSCODE_FIELD.search(label) and not (el.input_value() or "").strip():
                    return el
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_code(text: str) -> str:
        """A 4-10 character code (with at least one digit) right after the words
        that introduce it: 'Your one-time password is 482913'."""
        m = re.search(r"(?:passcode|password|code|pin)\W{0,3}(?:is|:)?\W{0,3}\b([A-Za-z0-9]{4,10})\b", text, re.IGNORECASE)
        if m and re.search(r"\d", m.group(1)):
            return m.group(1)
        m = re.search(r"\b(\d{4,8})\b", text)
        return m.group(1) if m else ""

    def passcode_from_gmail(self, page: Page, previous: str = "", wait_seconds: int = 150) -> str:
        """Reads the newest one-time passcode email (last hour) in the Gmail this
        browser is signed in to, in a separate tab. Opens only that one email,
        and only when its preview doesn't already show the code."""
        employer = (getattr(self, "employer", "") or "").strip()
        tab = page.context.new_page()
        try:
            deadline = time.time() + wait_seconds
            while time.time() < deadline:
                query = quote("newer_than:1h (passcode OR \"one-time\" OR verification OR code)")
                tab.goto(f"https://mail.google.com/mail/u/0/#search/{query}", wait_until="domcontentloaded", timeout=45_000)
                try:
                    tab.locator("tr.zA, td.TC").first.wait_for(state="attached", timeout=25_000)
                except Exception:
                    pass
                tab.wait_for_timeout(2_000)
                if "mail.google.com" not in tab.url:
                    logger.warning("PASSCODE: this browser isn't signed in to Gmail")
                    return ""
                rows = tab.locator("tr.zA")
                for i in range(min(rows.count(), 10)):  # newest first
                    text = " ".join(rows.nth(i).inner_text().split())
                    if not self._CODE_MAIL.search(text):
                        continue
                    if employer and employer.lower() not in text.lower() and i > 0:
                        continue  # prefer this employer's mail beyond the very newest
                    code = self._extract_code(text)
                    if not code:
                        rows.nth(i).click()
                        tab.wait_for_timeout(3_000)
                        body = " ".join(tab.locator("div.a3s, [role=main]").first.inner_text().split())
                        code = self._extract_code(body)
                    if code and code != previous:
                        logger.info("PASSCODE: found a one-time passcode in Gmail (%s...)", text[:60])
                        return code
                    break
                logger.info("PASSCODE: no new passcode email yet -- checking again in 15s")
                tab.wait_for_timeout(15_000)
            logger.warning("PASSCODE: no passcode email arrived within %ds", wait_seconds)
            return ""
        except Exception as exc:
            logger.warning("PASSCODE: Gmail read failed: %s", str(exc).splitlines()[0][:160])
            return ""
        finally:
            try:
                tab.close()
                page.bring_to_front()
            except Exception:
                pass

    def complete_emailed_passcode(self, page: Page) -> bool:
        if not safety.password_allowed(page.url):
            return False  # never a Google/Apple/Microsoft verification step
        """On a 'we've sent a one-time password to your email' step of account
        setup or sign-in: read the code from Gmail, enter it, continue. The
        user's decision (2026-09-15). If the code is rejected or expired, asks
        for a new one once."""
        field = self._passcode_field(page)
        if field is None:
            return False
        previous = ""
        for attempt in (1, 2):
            code = self.passcode_from_gmail(page, previous=previous)
            if not code:
                return False
            field = self._passcode_field(page) or field
            field.click()
            field.fill("")
            field.press_sequentially(code, delay=40)
            button = page.get_by_role("button", name=re.compile(r"^\s*(continue|verify|submit code|confirm|next)\s*$", re.IGNORECASE))
            button = next((button.nth(i) for i in range(button.count()) if button.nth(i).is_visible()), None)
            if button is None:
                button = page.locator("input[type=submit][value*='Continue' i], input[type=submit][value*='Verify' i]").first
            if button is not None and button.count():
                self._click_resiliently(button, timeout_ms=8_000)
            else:
                field.press("Enter")
            page.wait_for_timeout(5_000)
            body = (page.locator("body").inner_text(timeout=5_000) or "").lower()
            if self._passcode_field(page) is None and not re.search(r"invalid|incorrect|expired", body):
                logger.info("PASSCODE_OK: entered the emailed passcode")
                return True
            logger.warning("PASSCODE_REJECTED (attempt %d): %s", attempt,
                           " ".join(re.findall(r"[^.\n]*(?:invalid|incorrect|expired)[^.\n]*", body))[:160])
            previous = code
            resend = page.locator("a, button").filter(has_text=re.compile(r"(request|send|get) (a )?new (passcode|code)|resend", re.IGNORECASE))
            if resend.count() and resend.first.is_visible():
                self._click_resiliently(resend.first, timeout_ms=5_000)
                page.wait_for_timeout(3_000)
        return False

    def find_submit_button(self, page: Page):
        """Locates the application's own final Submit button so the agent knows
        it is on the form and can hand over -- it is never clicked. Checked in
        order of how specific the label is, skipping hidden buttons and pop-ups:
        a single CSS list returned the first match in page order, and on CBTS
        every 'Read more' toggle is a type=submit button."""
        named = safety.SUBMIT_LABEL_RE
        checks = [(page.get_by_role("button", name=named), False)]
        checks += [(page.locator(sel), True) for sel in ("input[type='submit']", "button[type='submit']")]
        for loc, needs_label in checks:
            for i in range(min(loc.count(), 10)):
                el = loc.nth(i)
                try:
                    if not el.is_visible() or self._in_popup(el):
                        continue
                    if needs_label:
                        label = (el.inner_text() or el.get_attribute("value") or "").strip()
                        if not named.search(label):
                            continue
                    return el
                except Exception:
                    continue
        return None

    def click_verified_submit(self, page: Page) -> bool:
        """Clicks the application's Submit button.

        The ONLY caller is apply_flow.submit_verified(), and only on an
        eligible safety.AutoSubmitDecision -- meaning the user turned
        AUTO_SUBMIT_VERIFIED_ONLY on, every required field matched approved
        data, the job and documents were verified, and nothing uncertain was on
        the page. This method re-checks the two conditions that can appear
        between the decision and the click, and refuses if either has.
        """
        if safety.captcha_visible(page):
            logger.error("SUBMIT_HELD: a CAPTCHA appeared -- not submitting")
            return False
        if self.pending_attestations(page):
            logger.error("SUBMIT_HELD: an attestation/signature is outstanding -- not submitting")
            return False
        button = self.find_submit_button(page)
        if button is None:
            logger.error("SUBMIT_HELD: no Submit button on this page")
            return False
        label = (button.inner_text() or "").strip()
        if not self._click_resiliently(button, timeout_ms=10_000):
            logger.error("SUBMIT_HELD: the Submit button could not be clicked (covered or disabled)")
            return False
        logger.info("Clicked %r", label[:40])
        page.wait_for_timeout(5_000)
        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        return True

    def wait_for_submission_evidence(self, page: Page, job_title: str = "",
                                     timeout_seconds: float = 240.0) -> Optional[str]:
        """Waits for the employer to confirm: a confirmation page, the job
        showing as applied in their portal, or a confirmation email.

        A click is not evidence. Without one of these, the caller records
        needs_user_review rather than assuming the application arrived.
        """
        company = getattr(self, "employer", "") or ""
        waited, mail_checked = 0.0, False
        while waited < timeout_seconds:
            try:
                if page.is_closed():
                    break
                if self.submission_confirmed(page, job_title):
                    return f"the page confirmed it ({page.url[:80]})"
            except Exception:
                pass
            if not mail_checked and waited >= 60 and company:
                mail_checked = True
                found = self.gmail_shows_confirmation(page, company, job_title)
                if found:
                    return f"confirmation email: {found[:120]}"
            time.sleep(5.0)
            waited += 5.0
        return None

    def refuse_to_submit(self, reason: str = "") -> None:
        """There is no code path in this class that clicks an application's
        Submit button. The user's specification says the agent stops before it,
        and the previous click_submit() has been removed so a future change
        can't quietly reintroduce it."""
        logger.warning(
            "SUBMIT_REFUSED: the agent never submits applications%s -- review the form in the "
            "browser window and click Submit yourself", f" ({reason})" if reason else "",
        )

    def read_back_fields(self, page: Page) -> list[dict]:
        """What is actually on the form right now: every visible answerable
        control with its label, value, whether it is required, and whether the
        agent or someone else put the value there.

        This is the input to the field-by-field comparison that verified
        auto-submit depends on -- it reads the page, never the agent's
        intentions.
        """
        try:
            items = page.evaluate("""() => {
                const visible = e => !!(e.offsetParent || e.getClientRects().length);
                const labelOf = el => {
                    const ids = (el.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean);
                    const byIds = ids.map(i => document.getElementById(i)?.innerText || '').join(' ').trim();
                    if (byIds) return byIds;
                    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
                    if (el.id) {
                        const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                        if (l && l.innerText.trim()) return l.innerText;
                    }
                    let n = el.parentElement;
                    for (let i = 0; i < 4 && n; i++, n = n.parentElement) {
                        const l = n.querySelector('label, legend');
                        if (l && l.innerText.trim() && !l.contains(el)) return l.innerText;
                    }
                    return el.name || el.id || '';
                };
                const out = [];
                for (const el of document.querySelectorAll('input, select, textarea')) {
                    const type = (el.type || '').toLowerCase();
                    if (['hidden', 'submit', 'button', 'file', 'image', 'password'].includes(type)) continue;
                    if (!visible(el) && type !== 'radio') continue;
                    let value = '';
                    if (type === 'checkbox') value = el.checked ? 'checked' : '';
                    else if (type === 'radio') {
                        if (!el.checked) continue;
                        value = (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)?.innerText)
                                || el.value || 'selected';
                    } else if (el.tagName === 'SELECT') {
                        value = el.selectedIndex >= 0 ? (el.options[el.selectedIndex]?.text || '') : '';
                    } else {
                        value = el.value || '';
                        if (!value) {
                            // A react-select combobox clears its search input
                            // after a choice and shows the value alongside it.
                            let n = el.parentElement;
                            for (let i = 0; i < 4 && n && !value; i++, n = n.parentElement) {
                                if (n.querySelectorAll('input:not([type=hidden]), select, textarea').length > 1) break;
                                const shown = n.querySelector('[class*=singleValue], [class*=single-value],'
                                                            + '[class*=multiValue], [class*=multi-value],'
                                                            + '[class*=selection-item]:not([class*=search])');
                                if (shown) value = shown.innerText.trim();
                            }
                        }
                    }
                    const label = (labelOf(el) || '').replace(/\\s+/g, ' ').trim();
                    out.push({
                        ref: el.id || el.name || '',
                        label: label.slice(0, 160),
                        value: String(value).slice(0, 200),
                        required: el.required || el.getAttribute('aria-required') === 'true' || /\\*\\s*$/.test(label),
                        type: el.tagName === 'SELECT' ? 'select' : type || 'text',
                    });
                }
                return out.slice(0, 200);
            }""")
        except Exception as exc:
            logger.warning("Could not read the form back: %s", str(exc).splitlines()[0][:120])
            return []

        fields = []
        for item in items:
            ref = item.get("ref") or ""
            selector = f"[id={json.dumps(ref)}]" if ref else ""
            item["source"] = "agent" if selector and self.values.is_ours(page, selector, item.get("value", "")) \
                else "site/user"
            fields.append(item)
        return fields

    def attached_document_names(self, page: Page) -> list[str]:
        """File names the form shows as attached, from its file inputs and from
        the filename labels platforms render instead."""
        try:
            return page.evaluate("""() => {
                const names = new Set();
                for (const el of document.querySelectorAll('input[type=file]')) {
                    for (const f of el.files || []) names.add(f.name);
                }
                const text = document.body ? document.body.innerText : '';
                const fileRe = new RegExp("[\\\\w.\\\\-()]+\\\\.(pdf|docx?|txt|rtf)\\\\b", "gi");
                for (const m of text.matchAll(fileRe)) names.add(m[0]);
                // Amazon lists the attachment as "Download <name>.pdf" inside a
                // panel innerText does not reach, so the resume it had just
                // taken was reported as not attached -- and uploaded again at
                // every step. textContent and the download link both see it.
                for (const el of document.querySelectorAll(
                        'a[href], [class*=document], [class*=file], [class*=attach], [data-filename]')) {
                    const seen = (el.textContent || '') + ' ' +
                                 (el.getAttribute('data-filename') || '') + ' ' +
                                 (el.getAttribute('href') || '');
                    for (const m of seen.matchAll(fileRe)) names.add(m[0]);
                }
                return [...names].slice(0, 20);
            }""")
        except Exception:
            return []

    def wait_out_captcha(self, page: Page, timeout_seconds: float = 600.0) -> bool:
        """A CAPTCHA is the user's to solve. The agent says so plainly, brings
        the window forward, and waits for it to disappear, then carries on with
        ordinary automation. It never touches the challenge itself."""
        if not safety.captcha_visible(page):
            return True
        self.raise_window(page)
        logger.warning("ACTION NEEDED: a CAPTCHA / human verification step is showing. "
                       "Please complete it in the browser window -- the agent will carry on by itself.")
        waited = 0.0
        while waited < timeout_seconds:
            time.sleep(5.0)
            waited += 5.0
            try:
                if page.is_closed():
                    return False
                if not safety.captcha_visible(page):
                    logger.info("CAPTCHA cleared -- resuming.")
                    return True
            except Exception:
                continue
        logger.warning("The CAPTCHA is still showing after %ds; handing over.", int(timeout_seconds))
        return False

    def validate_application(self, page: Page, resume_name: str = "") -> dict:
        """Everything a person needs to know before deciding to submit.

        Returns {required_still_blank, errors_shown, unanswered_questions,
        attestations_pending, captcha, resume_attached, page_url}. This is the
        agent's last act on a form: it validates, reports and stops.
        """
        report = {"required_still_blank": [], "errors_shown": [], "unanswered_questions": [],
                  "attestations_pending": [], "warnings_shown": [], "ambiguous_choices": [],
                  "unsupported_questions": [], "identity_checks": [], "attached_documents": [],
                  "captcha": False, "resume_attached": None, "page_url": "",
                  # True when Next did nothing: the page is waiting for
                  # something the agent has not done, and this is not the end.
                  "wizard_stuck": bool(getattr(self, "_stuck_on", None))}
        try:
            report["page_url"] = page.url
            blanks = self.find_required_blanks(page)
            report["required_still_blank"] = [" ".join(b.split()) for b in blanks["required_still_blank"]]
            report["errors_shown"] = [" ".join(e.split()) for e in blanks["errors_shown"]]
            report["captcha"] = safety.captcha_visible(page)
            report["warnings_shown"] = self._page_warnings(page)
            report["ambiguous_choices"] = list(getattr(self, "_ambiguous_choices", []))
            report["unsupported_questions"] = list(getattr(self, "_unsupported_questions", []))
            report["identity_checks"] = self._identity_checks(page)
            report["attached_documents"] = self.attached_document_names(page)
            if resume_name:
                report["resume_attached"] = self._file_already_attached(page, resume_name)
            # Signatures and attestations: listed for the user, never filled.
            report["attestations_pending"] = self.pending_attestations(page)
        except Exception as exc:
            report["errors_shown"].append(f"validation failed: {str(exc).splitlines()[0][:120]}")
        return report

    @staticmethod
    def _page_warnings(page: Page) -> list[str]:
        """Warning banners the form shows (distinct from hard errors)."""
        try:
            found = page.evaluate("""() => [...document.querySelectorAll('[class*=warn i], [role=status], [class*=alert i]')]
                .filter(e => (e.offsetParent || e.getClientRects().length) && (e.innerText || '').trim())
                .map(e => e.innerText.replace(/\\s+/g, ' ').trim().slice(0, 160)).slice(0, 10)""")
        except Exception:
            return []
        return [w for w in dict.fromkeys(found) if not re.search(r"^\s*(ok|success|saved)\b", w, re.IGNORECASE)]

    @staticmethod
    def _identity_checks(page: Page) -> list[str]:
        """Identity/verification steps (a code, an ID upload, 2-step) that only
        the user can complete."""
        try:
            body = page.locator("body").inner_text(timeout=5_000) or ""
        except Exception:
            return []
        found = []
        for pattern, label in (
            (r"one[- ]time (password|code)|verification code|passcode", "a one-time code step"),
            (r"two[- ]factor|2-step verification|authenticator app", "a two-factor step"),
            (r"upload (a )?(photo )?id\b|government[- ]issued id|identity verification", "an identity document check"),
        ):
            if re.search(pattern, body, re.IGNORECASE):
                found.append(label)
        return found

    def note_ambiguous_choice(self, question: str, offered: list[str], wanted: str) -> None:
        """Records a dropdown whose options don't contain the approved value.
        The agent leaves it alone; auto-submit refuses while any are recorded."""
        entry = f"{question[:70]!r}: wanted {wanted!r}, offered {offered[:4]}"
        self.__dict__.setdefault("_ambiguous_choices", [])
        if entry not in self._ambiguous_choices:
            self._ambiguous_choices.append(entry)

    def clear_ambiguous(self, question: str) -> None:
        """Drops an earlier 'no matching option' note for a question that has
        since been answered, so it doesn't hold up the hand-over report."""
        key = f"{question[:70]!r}:"
        self.__dict__.setdefault("_ambiguous_choices", [])
        self._ambiguous_choices = [e for e in self._ambiguous_choices if not e.startswith(key)]

    def note_unsupported_question(self, question: str) -> None:
        """Records a custom question with no approved answer."""
        self.__dict__.setdefault("_unsupported_questions", [])
        entry = " ".join(question.split())[:120]
        if entry and entry not in self._unsupported_questions:
            self._unsupported_questions.append(entry)

    def pending_attestations(self, page: Page) -> list[str]:
        """Unticked declarations and empty signature boxes on the page."""
        try:
            items = page.evaluate("""() => {
                const visible = e => !!(e.offsetParent || e.getClientRects().length);
                const out = [];
                for (const el of document.querySelectorAll('input[type=checkbox], input[type=text], input:not([type]), textarea')) {
                    if (!visible(el)) continue;
                    const label = ((el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)?.innerText)
                        || el.closest('label')?.innerText || el.getAttribute('aria-label')
                        || el.closest('.RCMFormField, .form-group, tr, div')?.innerText || '').trim();
                    if (!label || label.length > 400) continue;
                    const empty = el.type === 'checkbox' ? !el.checked : !(el.value || '').trim();
                    if (empty) out.push(label.replace(/\\s+/g, ' ').slice(0, 160));
                }
                return out.slice(0, 40);
            }""")
        except Exception:
            return []
        pending, seen = [], set()
        for label in items:
            if safety.is_attestation(label) and label.lower() not in seen:
                seen.add(label.lower())
                pending.append(label)
        return pending

    def save_review_package(
        self,
        page: Page,
        job_dir: Path,
        fields_summary: list[dict],
        questions_summary: list[dict],
        unfilled_matches: Optional[list[dict]] = None,
    ) -> Path:
        """Writes a screenshot + a plain JSON summary of every field and
        every drafted screening-question answer, for a human to review
        (via chat, since this may run without a live terminal) before any
        submit decision is made. `fields_filled` only lists fields that were
        genuinely filled in (non-empty value, no error) -- `unfilled_matches`
        surfaces fields we recognized but had nothing to put in them, so a
        human knows exactly what's still blank and needs their attention."""
        job_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = job_dir / "review_screenshot.png"
        self.accept_consent_dialog(page)
        page.screenshot(path=str(screenshot_path), full_page=True)
        try:  # the page's markup beside the screenshot, for diagnosing unfamiliar forms
            (job_dir / "review_page.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        leftovers = self.find_required_blanks(page)
        for label in leftovers["required_still_blank"]:
            logger.warning("REQUIRED_BLANK: %s", " ".join(label.split()))
        try:
            for item in page.evaluate("""() => {
                const out = [];
                for (const el of document.querySelectorAll('select, input[type=radio]')) {
                    if (!el.getClientRects().length) continue;
                    const req = el.required || el.getAttribute('aria-required') === 'true';
                    const lab = (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)?.innerText) || '';
                    const chosen = el.tagName === 'SELECT' ? el.options[el.selectedIndex] : null;
                    const blank = !chosen || !(chosen.value || '').trim()
                        || !(el.selectedIndex > 0 || chosen.defaultSelected || el.options.length === 1)
                        || /^[\\s\\-–—]*(no selection|select( one| an option)?|please select|choose( one)?|make a selection|none selected)?[\\s\\-–—.]*$/i.test(chosen.text);
                    if (el.tagName === 'SELECT' && blank) {
                        out.push({q: lab || el.getAttribute('aria-label') || el.name, options: [...el.options].map(o => o.text.trim()).slice(0, 12)});
                    }
                }
                return out.slice(0, 25);
            }"""):
                if item["q"]:
                    logger.info("UNANSWERED_SELECT: %s | options: %s", " ".join(item["q"].split())[:110], item["options"])
        except Exception:
            pass
        for message in leftovers["errors_shown"]:
            logger.warning("FORM_ERROR: %s", message)
        summary = {
            "screenshot": str(screenshot_path),
            "required_still_blank": leftovers["required_still_blank"],
            "errors_shown": leftovers["errors_shown"],
            "fields_filled": fields_summary,
            "fields_matched_but_still_blank": unfilled_matches or [],
            "screening_answers": questions_summary,
        }
        summary_path = job_dir / "review_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        logger.info("Review package written to %s", summary_path)
        return summary_path

    def raise_window(self, page: Page) -> None:
        """Puts the agent's browser window in front of other apps. Windows
        won't let a background process simply take focus -- the window opens
        behind whatever you're using (the IGT run's window was never seen) --
        but minimizing and re-maximizing it brings it to the top."""
        try:
            page.bring_to_front()
            if os.name != "nt":
                return
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            title = (page.title() or "").strip()
            found: list[int] = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def visit(hwnd, _):
                length = user32.GetWindowTextLengthW(hwnd)
                if length and user32.IsWindowVisible(hwnd):
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    text = buf.value
                    if "Chrome for Testing" in text and (not title or title[:40] in text):
                        found.append(hwnd)
                return True

            user32.EnumWindows(visit, 0)
            for hwnd in found[:1]:
                user32.ShowWindow(hwnd, 6)   # SW_MINIMIZE
                user32.ShowWindow(hwnd, 3)   # SW_MAXIMIZE
                user32.SetForegroundWindow(hwnd)
                logger.info("Brought the agent's browser window to the front")
        except Exception as exc:
            logger.debug("Could not raise the browser window: %s", exc)

    def wait_for_signal(
        self, signal_path: Path, poll_seconds: float = 2.0, timeout_seconds: float = 1800.0,
        page: Optional[Page] = None, left_form_seconds: float = 600.0,
    ) -> str:
        """Blocks (polling, not input()) until a signal file is written
        externally -- e.g. by a separate command once a human has reviewed
        the review package and replied with a decision. Returns the file's
        stripped, lowercased contents ('submit' or 'skip') and deletes it.

        Given the page, it also watches the browser itself and returns
        'submitted_by_user' when the human clicks Submit there, or
        'browser_closed' when they close the window -- otherwise the run sat
        waiting (and showed 'running') long after the application was in."""
        logger.info("Waiting for review signal at %s ...", signal_path)
        # Which job this is, from the job file saved beside the signal file --
        # needed to recognise it in a site's "My applications" list.
        job_title = company = ""
        try:
            job_file = signal_path.with_name(signal_path.name.replace("_signal_", "_job_", 1)).with_suffix(".json")
            job_info = json.loads(job_file.read_text(encoding="utf-8-sig"))
            job_title, company = job_info.get("title", ""), job_info.get("company", "")
        except Exception:
            pass
        profile = getattr(self, "_profile", None)
        check_mail = bool(getattr(profile, "check_gmail_for_confirmation", True))
        if page is not None:
            self.raise_window(page)
        waited = 0.0
        away_since: Optional[float] = None
        last_list_reload = 0.0
        last_mail_check: Optional[float] = None
        # Stopped for a CAPTCHA: once the user has completed it, carry on by
        # itself. On Schwab's sign-in the user solved the puzzle, the site moved
        # to its next step, and the run went on waiting for an instruction.
        waiting_on_captcha = False
        captcha_gone_polls = 0
        if page is not None:
            try:
                waiting_on_captcha = safety.captcha_visible(page)
            except Exception:
                waiting_on_captcha = False
            if waiting_on_captcha:
                logger.info("Waiting for you to complete the CAPTCHA; the agent carries on once it is done")
        while not signal_path.exists():
            time.sleep(poll_seconds)
            waited += poll_seconds
            # Checked first: the page checks below can `continue`, and a skipped
            # timeout left a run on a sign-in page waiting forever.
            if waited >= timeout_seconds:
                raise TimeoutError(f"No review signal received within {timeout_seconds}s")
            if page is not None:
                if page.is_closed():
                    return "browser_closed"
                if waiting_on_captcha:
                    try:
                        captcha_gone_polls = 0 if safety.captcha_visible(page) else captcha_gone_polls + 1
                    except Exception:
                        captcha_gone_polls = 0  # mid-navigation: look again next poll
                    if captcha_gone_polls >= 2:
                        logger.info("The CAPTCHA is done -- carrying on with the application")
                        page.wait_for_timeout(2_000)  # let the site's next step finish loading
                        return "refresh"
                    continue
                try:
                    on_form = self.find_submit_button(page) is not None
                    if on_form:
                        self._seen_application_form = True
                    # A confirmation page counts even if the form was never
                    # recognised (it needs its message AND no Submit button).
                    if not on_form and self.submission_confirmed(page, job_title):
                        return "submitted_by_user"
                    if not getattr(self, "_seen_application_form", False):
                        # Still before the form (a sign-in page, say): nothing
                        # can have been submitted, so no Gmail checks and no
                        # "left the form" timer -- just wait for the user.
                        continue
                    if self.submission_confirmed(page, job_title):
                        return "submitted_by_user"
                    if on_form:
                        away_since = None
                    else:
                        away_since = waited if away_since is None else away_since
                        # An applications list may not show a brand-new
                        # submission until reloaded.
                        if re.search(r"/applications|my-applications|dashboard", page.url) and waited - last_list_reload >= 60:
                            last_list_reload = waited
                            page.reload(wait_until="domcontentloaded", timeout=30_000)
                        # The site shows nothing conclusive: look for the
                        # employer's confirmation email, every 2 minutes.
                        if check_mail and (last_mail_check is None or waited - last_mail_check >= 120):
                            last_mail_check = waited
                            found = self.gmail_shows_confirmation(page, company, job_title)
                            if found:
                                self._confirmation_evidence = f"confirmation email: {found}"
                                return "submitted_by_user"
                        if waited - away_since >= left_form_seconds:
                            return "left_form"
                except Exception as exc:
                    if page.is_closed() or "closed" in str(exc).lower():
                        return "browser_closed"
                    # Mid-navigation (the confirmation page loading) -- try again next poll.
        # utf-8-sig, not utf-8: PowerShell's Set-Content -Encoding utf8 writes
        # a BOM, and a leading BOM made 'reload_code' miss every branch and
        # fall through to "skip", silently abandoning a live application.
        raw = signal_path.read_text(encoding="utf-8-sig").strip().strip("﻿")
        # "goto:<url>" keeps its capitals: an Amazon application path is
        # /en-US/..., and lowercasing it leads somewhere else.
        decision = raw if raw.lower().startswith("goto:") else raw.lower()
        signal_path.unlink()
        logger.info("Received review signal: %s", decision)
        if decision.lower().startswith("goto:"):
            # The tab wandered off the application -- a sign-in redirect, or
            # the user browsing. This puts it back without restarting the run
            # and losing the part-filled form. Handled here rather than in the
            # flow so it reaches a run that is already open, via reload_code.
            destination = decision[len("goto:"):].strip()
            if page is None:
                logger.warning("GOTO: no browser page to navigate")
                return "refresh"
            logger.info("GOTO: returning the browser to %s", destination)
            try:
                page.goto(destination, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(2_000)
            except Exception as exc:
                logger.warning("Could not return to %s: %s", destination, exc)
            return "refresh"  # re-detect whatever is on the page now
        return decision

    def pause_for_human_review(self, page: Page, job_title: str = "", company: str = "") -> None:
        """Never auto-submits. Screenshots the filled form and blocks the
        program until the human confirms in the terminal that they have
        reviewed everything and (if satisfied) clicked Submit themselves
        in the visible browser window."""
        screenshot_path = self._config.output_dir / "last_form_review.png"
        page.screenshot(path=str(screenshot_path), full_page=True)
        logger.info("Screenshot saved to %s for your review", screenshot_path)
        heading = f"{job_title} @ {company}" if job_title else "this application"
        input(
            f"\n>>> FINAL REVIEW before submitting to {heading}:\n"
            ">>>   [ ] Job title, location, and salary match what you expected\n"
            ">>>   [ ] Tailored resume text is accurate -- nothing fabricated or exaggerated\n"
            ">>>   [ ] Cover letter has the right company/role name and reads like you\n"
            ">>>   [ ] Every auto-filled field in the browser is actually correct\n"
            ">>>   [ ] Any extra questions the assistant couldn't fill (EEO, work auth,\n"
            ">>>       salary expectation, etc.) are answered by you\n"
            ">>>   [ ] The correct resume file is attached\n"
            ">>> Only click Submit in the browser once every box above is true.\n"
            ">>> Press Enter here once you're done (submitted or not) to continue...\n"
        )
