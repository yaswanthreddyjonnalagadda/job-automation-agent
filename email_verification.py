"""Reading the owner's mail for an emailed sign-in code or verification link, and the steps that use one.

Moved out of browser_automation.JobApplicationAssistant (code-quality audit, step 3.1), verbatim: these methods only
need `self` (the assistant's page helpers and settings), so they live here as a mixin the assistant inherits from.
Whether a code or link may be read at all is decided in emailed_codes.why_not() and nowhere else; nothing here
re-decides it.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional
from urllib.parse import quote, urlparse

from playwright.sync_api import Page

import emailed_codes
import login_guard
import safety
from interaction import type_text

try:
    from runtime_events import EventName, ReasonCode, RuntimeEvent
except Exception:                                   # the event bus is optional here, as in browser_automation
    RuntimeEvent = None

logger = logging.getLogger("browser_automation")


class EmailVerificationMixin:
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
        keep_mail_open = False
        try:
            query = quote(f"{company} newer_than:2d")
            tab.goto(self._gmail_search_url(query), wait_until="domcontentloaded", timeout=45_000)
            try:
                tab.locator("tr.zA, td.TC").first.wait_for(state="attached", timeout=25_000)
            except Exception as _swallowed:
                logger.debug("gmail_shows_confirmation: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)
            tab.wait_for_timeout(2_000)
            if not self._gmail_mailbox_matches(tab):
                keep_mail_open = True
                return None

            rows = tab.locator("tr.zA")
            texts = [" ".join(t.split()) for t in rows.all_inner_texts()[:25]]
            if not texts:  # Gmail markup changed: fall back to the results pane's text
                main = tab.locator("[role=main]").first
                texts = [" ".join(t.split()) for t in (main.inner_text() if main.count() else "").split("\n") if t.strip()]
            wanted = [w.lower() for w in (company, job_title) if w]
            for text in texts:
                low = text.lower()
                # The job itself, not just the employer: an older application
                # to another Schwab job was taken as this one's confirmation.
                names_job = job_title.lower() in low if job_title else company.lower() in low
                if names_job and self._CONFIRMATION_MAIL.search(text):
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
                if not keep_mail_open:
                    tab.close()
                    page.bring_to_front()
            except Exception as _swallowed:
                logger.debug("gmail_shows_confirmation: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)

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
        except Exception as _swallowed:
            logger.debug("_passcode_field: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)
        return None

    _PLAIN_MAILBOX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

    def _gmail_search_url(self, query: str) -> str:
        """The Gmail search for `query` in the account the job accounts use (f182), not whichever Google account
        comes first in this browser: the owner has more than one. This URL is a navigation hint:
        Gmail may silently fall back to another account. Readers must verify the visible account
        identity before accessing messages. An address that is not a plain mailbox is never put into the URL."""
        email = (getattr(getattr(self, "_config", None), "ats_email", "") or "").strip()
        if not email:
            email = (getattr(self._owner_profile(), "email", "") or "").strip()
        if self._PLAIN_MAILBOX.match(email):
            # /mail/u/<address>/ is unsupported. authuser is a hint, never proof of identity.
            return f"https://mail.google.com/mail/?authuser={quote(email)}#search/{query}"
        return f"https://mail.google.com/mail/u/0/#search/{query}"

    def _gmail_mailbox_matches(self, tab: Page) -> bool:
        """The account button, not a navigation hint, establishes mailbox identity."""
        email = (getattr(getattr(self, "_config", None), "ats_email", "") or "").strip()
        email = email or (getattr(self._owner_profile(), "email", "") or "").strip()
        if self._PLAIN_MAILBOX.fullmatch(email) and urlparse(tab.url).hostname == "mail.google.com":
            button = tab.get_by_role("button", name=re.compile(r"^Google Account:", re.I))
            try:
                button.first.wait_for(state="visible", timeout=5_000)
                label = button.first.get_attribute("aria-label") or button.first.inner_text()
                addresses = re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", label)
                if len(addresses) == 1 and addresses[0].casefold() == email.casefold():
                    return True
            except Exception as _swallowed:
                logger.debug("_gmail_mailbox_matches: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)
        self._login_paused = (
            "The applying Gmail account is not confirmed in this browser. "
            "Sign in to the application email in the open Google tab, then press Continue.")
        if self._PLAIN_MAILBOX.fullmatch(email):
            tab.goto("https://accounts.google.com/AddSession?service=mail&continue="
                     "https%3A%2F%2Fmail.google.com%2Fmail%2F&login_hint=" + quote(email),
                     wait_until="domcontentloaded", timeout=45_000)
        return False

    def passcode_from_gmail(self, page: Page, previous: str = "", wait_seconds: int = 150,
                            length: int = 0) -> str:
        """Reads the newest one-time passcode email (last hour) in the Gmail this
        browser is signed in to, in a separate tab. Opens only that one email,
        and only when its preview doesn't already show the code.

        Every reader of a code comes through here, so the one rule for when a code may be read
        (emailed_codes.why_not) is asked before the mail is opened: no caller can go around it."""
        why = self.why_not_read_a_code(page)
        if why:
            logger.info("PASSCODE: %s -- leaving it to the user", why)
            return ""
        employer = (getattr(self, "employer", "") or "").strip()
        app_key = getattr(self, "application_key", "")
        tab = page.context.new_page()
        keep_mail_open = False
        try:
            deadline = time.time() + wait_seconds
            while time.time() < deadline:
                if RuntimeEvent is not None:
                    RuntimeEvent.emit(
                        event_name=EventName.EMAIL_POLLING,
                        component="gmail",
                        stage="email_verification",
                        display_message=f"Polling Gmail for verification passcode ({employer or 'employer'})",
                        application_key=app_key,
                        safe_metadata={"employer": employer},
                    )
                query = quote("newer_than:1h (passcode OR \"one-time\" OR verification OR code)")
                tab.goto(self._gmail_search_url(query), wait_until="domcontentloaded", timeout=45_000)
                try:
                    tab.locator("tr.zA, td.TC").first.wait_for(state="attached", timeout=25_000)
                except Exception as _swallowed:
                    logger.debug("passcode_from_gmail: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)
                tab.wait_for_timeout(2_000)
                if not self._gmail_mailbox_matches(tab):
                    keep_mail_open = True
                    return ""
                rows = tab.locator("tr.zA")
                for i in range(min(rows.count(), 10)):  # newest first
                    text = " ".join(rows.nth(i).inner_text().split())
                    if not self._CODE_MAIL.search(text):
                        continue
                    if employer and employer.lower() not in text.lower() and i > 0:
                        continue  # prefer this employer's mail beyond the very newest
                    code = self._extract_code(length=length, text=text)
                    if not code:
                        rows.nth(i).click()
                        tab.wait_for_timeout(3_000)
                        body = " ".join(tab.locator("div.a3s, [role=main]").first.inner_text().split())
                        code = self._extract_code(length=length, text=body)
                    if code and code != previous:
                        logger.info("PASSCODE: found a one-time passcode in Gmail")  # never the mail's text: it holds the code
                        if RuntimeEvent is not None:
                            RuntimeEvent.emit(
                                event_name=EventName.EMAIL_MATCHED,
                                component="gmail",
                                stage="email_verification",
                                display_message="Found verification passcode in Gmail",
                                is_verified=True,
                                application_key=app_key,
                                safe_metadata={"employer": employer},
                            )
                        login_guard.record_code_read(
                            urlparse(page.url).netloc.lower(),
                            (getattr(getattr(self, "_config", None), "ats_email", "") or "").strip()
                            or getattr(self._owner_profile(), "email", ""))
                        return code
                    break
                logger.info("PASSCODE: no new passcode email yet -- checking again in 15s")
                tab.wait_for_timeout(15_000)
            logger.warning("PASSCODE: no passcode email arrived within %ds", wait_seconds)
            if RuntimeEvent is not None:
                RuntimeEvent.emit(
                    event_name=EventName.EMAIL_TIMEOUT,
                    component="gmail",
                    stage="email_verification",
                    reason_code=ReasonCode.EMAIL_VERIFICATION_TIMEOUT,
                    display_message=f"Passcode email did not arrive within {wait_seconds}s",
                    application_key=app_key,
                )
            return ""
        except Exception as exc:
            logger.warning("PASSCODE: Gmail read failed: %s", str(exc).splitlines()[0][:160])
            return ""
        finally:
            try:
                if not keep_mail_open:
                    tab.close()
                    page.bring_to_front()
            except Exception as _swallowed:
                logger.debug("passcode_from_gmail: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)

    def verification_link_from_gmail(self, page: Page, wait_seconds: int = 150) -> str:
        """The newest account-verification link (last hour) the site at `page` emailed the owner, read from the Gmail
        this browser is signed in to, in a separate tab; "" when there is none or the owner's rule does not allow it.

        Asked through emailed_codes.why_not (the owner's permission to read mail, an employer site, no CAPTCHA, the
        account's limit) and verification_link_ok (a link back to the same site that verifies an account, never a
        password reset). The link is never written to the log: it is the account's key."""
        why = self.why_not_read_a_code(page)
        if why:
            logger.info("VERIFY_LINK: %s -- leaving it to the user", why)
            return ""
        site_url = page.url
        app_key = getattr(self, "application_key", "")
        tab = page.context.new_page()
        keep_mail_open = False
        try:
            deadline = time.time() + wait_seconds
            while time.time() < deadline:
                if RuntimeEvent is not None:
                    RuntimeEvent.emit(
                        event_name=EventName.EMAIL_POLLING,
                        component="gmail",
                        stage="email_verification",
                        display_message="Polling Gmail for account verification link",
                        application_key=app_key,
                        safe_metadata={"site_url": site_url},
                    )
                query = quote("newer_than:1h (verify OR verification OR activate OR confirm)")
                tab.goto(self._gmail_search_url(query), wait_until="domcontentloaded",
                         timeout=45_000)
                try:
                    tab.locator("tr.zA, td.TC").first.wait_for(state="attached", timeout=25_000)
                except Exception as _swallowed:
                    logger.debug("verification_link_from_gmail: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)
                tab.wait_for_timeout(2_000)
                if not self._gmail_mailbox_matches(tab):
                    keep_mail_open = True
                    return ""
                rows = tab.locator("tr.zA")
                for i in range(min(rows.count(), 5)):          # newest first
                    rows.nth(i).click()
                    tab.wait_for_timeout(3_000)
                    anchors = tab.locator("div.a3s a[href]")
                    for j in range(min(anchors.count(), 60)):
                        anchor = anchors.nth(j)
                        href = anchor.get_attribute("href") or ""
                        text = " ".join((anchor.inner_text() or "").split())
                        if emailed_codes.verification_link_ok(href, text, site_url):
                            logger.info("VERIFY_LINK: found the account-verification link from %s in Gmail",
                                        urlparse(emailed_codes.unwrap(href)).netloc)
                            if RuntimeEvent is not None:
                                RuntimeEvent.emit(
                                    event_name=EventName.EMAIL_MATCHED,
                                    component="gmail",
                                    stage="email_verification",
                                    display_message="Found account verification link in Gmail",
                                    is_verified=True,
                                    application_key=app_key,
                                    safe_metadata={"domain": urlparse(site_url).netloc},
                                )
                            login_guard.record_code_read(
                                urlparse(site_url).netloc.lower(),
                                (getattr(getattr(self, "_config", None), "ats_email", "") or "").strip()
                                or getattr(self._owner_profile(), "email", ""))
                            return emailed_codes.unwrap(href)
                    tab.go_back(wait_until="domcontentloaded", timeout=30_000)
                    tab.wait_for_timeout(1_500)
                    rows = tab.locator("tr.zA")
                logger.info("VERIFY_LINK: no verification email from this site yet -- checking again in 15s")
                tab.wait_for_timeout(15_000)
            logger.warning("VERIFY_LINK: no verification email arrived within %ds", wait_seconds)
            if RuntimeEvent is not None:
                RuntimeEvent.emit(
                    event_name=EventName.EMAIL_TIMEOUT,
                    component="gmail",
                    stage="email_verification",
                    reason_code=ReasonCode.EMAIL_VERIFICATION_TIMEOUT,
                    display_message=f"Verification link email did not arrive within {wait_seconds}s",
                    application_key=app_key,
                )
            return ""
        except Exception as exc:
            logger.warning("VERIFY_LINK: Gmail read failed: %s", str(exc).splitlines()[0][:160])
            return ""
        finally:
            try:
                if not keep_mail_open:
                    tab.close()
                    page.bring_to_front()
            except Exception as _swallowed:
                logger.debug("verification_link_from_gmail: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)

    def complete_emailed_passcode(self, page: Page) -> bool:
        """On a 'we've sent a one-time password to your email' step of account
        setup or sign-in: read the code from Gmail, enter it, continue. The
        user's decision (2026-09-15). If the code is rejected or expired, asks
        for a new one once. Whether a code may be read at all is emailed_codes.why_not's."""
        field = self._passcode_field(page)
        if field is None:
            return False
        why = self.why_not_read_a_code(page)
        if why:
            logger.info("PASSCODE: %s -- leaving it to the user", why)
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
            elif self.find_submit_button(page) is not None:
                # The code sits on the application itself (Greenhouse's security code): the next press is the
                # application's Submit, and that is the owner's. Enter would be that press.
                logger.info("CODE: entered -- the next press on this page is the application's Submit, which is yours")
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

    # ------------------------------------------------------------------
    # An account that already exists for the owner's email
    # ------------------------------------------------------------------
    RECOVERY_WAIT_SECONDS = 25

    _FORGOT = re.compile(
        r"forgot(?:ten)?(?: your| my)? pass ?word|reset(?: your| my)? pass ?word|"
        r"trouble (?:signing|logging) in|can'?t (?:sign|log) in", re.IGNORECASE)

    _SEND_STEP = re.compile(
        r"^\s*(?:send(?: me)?(?: a| the)?(?: code| email| link| instructions)?|continue|next|submit|"
        r"reset(?: password)?|request(?: a)?(?: code)?)\s*$", re.IGNORECASE)

    _LINK_ONLY = re.compile(
        r"(?:reset|password) link|link (?:has been |was )?(?:sent|emailed)|click (?:on )?the link|"
        r"emailed you a link|instructions to reset", re.IGNORECASE)

    _CODE_REFUSED = re.compile(r"invalid|incorrect|expired|didn'?t match|not valid|wrong code", re.IGNORECASE)

    _PASSWORD_REFUSED = re.compile(
        r"does not meet|must (?:contain|be|include|have)|too (?:short|weak|common)|cannot (?:be|reuse)|"
        r"same as|previous password|recent password|not allowed|requirements|do not match|don'?t match",
        re.IGNORECASE)

    _RESET_DONE = re.compile(
        r"password (?:has been |was )?(?:reset|updated|changed|set)|successfully", re.IGNORECASE)

    def _reset_password_with_emailed_code(self, page: Page, email: str, password: str) -> bool:
        domain = urlparse(page.url).netloc.lower()
        if not safety.password_allowed(page.url) or safety.captcha_visible(page):
            return False
        # Once per site per run, whichever route asked for it (an 'account exists' form or a refused sign-in).
        reset_on = self.__dict__.setdefault("_reset_requested_on", set())
        if domain in reset_on:
            logger.info("RECOVERY: a reset was already requested on %s this run -- leaving it to the user", domain)
            return False
        if getattr(self.adapter(page), "name", "") == "workday":
            # Workday resets by an emailed link that lasts about two hours and allows five requests in 24
            # hours; the agent cannot follow a link, so a request only spends one of the five. (Asked first:
            # this is the reason whatever the owner has allowed, and it is the more useful thing to say.)
            self._login_paused = ("Workday resets a password with a link it emails you (valid about 2 hours, five "
                                  "requests in 24 hours): use 'Forgot your password?' yourself, then press Continue")
            logger.info("RECOVERY: %s -- not requested by the agent", self._login_paused)
            return False
        why = self.why_not_read_a_code(page, email)        # before Forgot is pressed: a request is spent by it
        if why:
            self._login_paused = why
            logger.info("RECOVERY: %s -- not requested by the agent", why)
            return False
        held = login_guard.may_request_reset(domain, email)
        if held:
            self._login_paused = held
            logger.info("RECOVERY: %s -- leaving it to the user", held)
            return False
        forgot = self._first_visible(page, ("link", "button"), self._FORGOT)
        if forgot is None:
            logger.info("RECOVERY: %s offers no forgot-password step -- leaving it to the user", domain)
            return False
        logger.info("RECOVERY: %s rejected the password -- resetting it to the existing one with an emailed code",
                    domain)
        reset_on.add(domain)
        self._click_resiliently(forgot, timeout_ms=5_000)
        page.wait_for_timeout(1_500)
        box = self._find_login_email_input(page)
        if box is not None and not (box.input_value() or "").strip():
            type_text(box, email)
        send = self._first_visible(page, ("button",), self._SEND_STEP)
        login_guard.record_reset_request(domain, email)
        if send is not None:
            self._click_resiliently(send, timeout_ms=8_000)
        elif box is not None:
            box.press("Enter")

        field, deadline = None, time.time() + self.RECOVERY_WAIT_SECONDS
        while time.time() < deadline:
            field = self._passcode_field(page)
            if field is not None:
                break
            if self._page_says(page, self._LINK_ONLY):
                self._login_paused = (f"{domain} emailed you a link to reset the password, not a code: open it, set "
                                      f"the password to the one in Settings, then press Continue")
                logger.info("RECOVERY: %s sends a link, not a code -- leaving it to the user", domain)
                return False
            page.wait_for_timeout(1_000)
        if field is None:
            self._login_paused = (f"{domain} was asked to reset the password, but no box for an emailed code came up: "
                                  f"look at the page (or the email it sent), set the password to the one in Settings, "
                                  f"then press Continue")
            logger.info("RECOVERY: no code step appeared on %s -- leaving it to the user", domain)
            return False

        typed, previous = False, ""
        for _attempt in (1, 2):
            code = self.passcode_from_gmail(page, previous=previous)
            if not code:
                self._login_paused = (f"no reset code from {domain} arrived in your Gmail: find the email, enter its "
                                      f"code with the password from Settings, then press Continue")
                logger.info("RECOVERY: no code arrived in Gmail -- leaving it to the user")
                return False
            field = self._passcode_field(page) or field
            field.click()
            field.fill("")
            field.press_sequentially(code, delay=40)
            boxes = self._visible_password_boxes(page)
            if boxes:
                typed = self._type_new_password(boxes, password)
                if not typed:
                    return False
            self._press_step(page, field)
            page.wait_for_timeout(2_000)
            if field.is_visible() and self._page_says(page, self._CODE_REFUSED):
                logger.warning("RECOVERY: %s refused the code", domain)
                previous = code
                continue
            break
        else:
            return False

        if not typed:
            boxes = self._visible_password_boxes(page)
            if not boxes:
                logger.info("RECOVERY: no new-password step appeared on %s -- leaving it to the user", domain)
                return False
            if not self._type_new_password(boxes, password):
                return False
            self._press_step(page, boxes[0])
            page.wait_for_timeout(2_000)

        still_asking = len(self._visible_password_boxes(page)) >= 2
        if still_asking and (self._page_says(page, self._PASSWORD_REFUSED)
                             or not self._page_says(page, self._RESET_DONE)):
            logger.warning("RECOVERY_REJECTED: %s did not take the existing password as the new one "
                           "-- leaving it to the user", domain)
            return False
        logger.info("RECOVERY_OK: %s now has the existing password", domain)
        login_guard.clear_hold(domain, email)      # the rejection that led here is over: try the new password
        scope = self._sign_in_scope(page)
        if scope is not None or len(self._visible_password_boxes(page)) == 1:
            return self.attempt_auto_login(page, email, password, scope=scope, create_if_missing=False)
        return True
