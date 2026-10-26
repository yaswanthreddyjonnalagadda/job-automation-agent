"""Waiting for the owner: the signal file, the waiting note beside it, and the wait for a submission's evidence.

Moved out of browser_automation.JobApplicationAssistant (code-quality audit, step 3.1), verbatim: these methods only need
`self`, so they live here as a mixin the JobApplicationAssistant inherits from.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from playwright.sync_api import Page
from typing import Optional
from urllib.parse import urlparse
import form_fields
import json
import phone_console
import provenance
import re
import safety
import time


logger = logging.getLogger("browser_automation")


def waiting_note_for(signal_path: Path) -> Path:
    """The note beside a signal file that says its run is waiting for the owner (_waiting_<name>.txt)."""
    signal_path = Path(signal_path)
    note = signal_path.with_name(signal_path.name.replace("_signal_", "_waiting_", 1))
    # A name without "_signal_" came back unchanged: the note overwrote the signal and was read as one (f193).
    return note if note != signal_path else signal_path.with_name("_waiting_" + signal_path.name)


class OwnerWaitMixin:
    def wait_for_submission_evidence(self, page: Page, job_title: str = "",
                                     timeout_seconds: float = 240.0) -> Optional[str]:
        """Waits for the employer to confirm: a confirmation page, the job
        showing as applied in their portal, or a confirmation email.

        A click is not evidence. Without one of these, the caller records
        needs_user_review rather than assuming the application arrived.
        """
        company = getattr(self, "employer", "") or ""
        waited, mail_attempts, last_mail_check = 0.0, 0, None
        check_mail = bool(getattr(getattr(self, "_profile", None), "check_gmail_for_confirmation", True))
        while waited < timeout_seconds:
            on_form = True
            try:
                if page.is_closed():
                    break
                if self.submission_confirmed(page, job_title):
                    return f"the page confirmed it ({page.url[:80]})"
                on_form = self.find_submit_button(page) is not None
            except Exception as _swallowed:
                logger.debug("wait_for_submission_evidence: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)
            if not on_form and check_mail and company and self._confirmation_email_due(waited, last_mail_check, mail_attempts):
                mail_attempts += 1
                last_mail_check = waited
                found = self.gmail_shows_confirmation(page, company, job_title)
                if found:
                    return f"confirmation email: {found[:120]}"
            time.sleep(5.0)
            waited += 5.0
        return None

    def wait_for_signal(
        self, signal_path: Path, poll_seconds: float = 2.0, timeout_seconds: float = 1800.0,
        page: Optional[Page] = None, left_form_seconds: float = 600.0, for_captcha: bool = False,
        for_blanks: bool = False,
    ) -> str:
        """Waits for the owner (see _wait_for_signal), with the page marked as
        the owner's turn, so every control they change is recorded as theirs."""
        if page is not None:
            provenance.set_agent_busy(page, False)
        # The dashboard offers Continue for a run that is waiting, which it knows from this note. It showed
        # Continue only for an answer already sent (the signal file), so a waiting run had no button at all
        # (Rackspace, 29 September).
        waiting = waiting_note_for(signal_path)
        try:
            waiting.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        except OSError as _swallowed:
            logger.debug("wait_for_signal: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)
        # A stop that needs the owner: tell their phone, and serve the console while waiting (both optional).
        needs = getattr(self, "_owner_needs", None)
        self._phone = None
        if needs:
            try:
                self._phone = phone_console.begin(signal_path, needs)
            except Exception as exc:
                logger.info("PHONE_CONSOLE: not started (%s)", type(exc).__name__)
        try:
            return self._wait_for_signal(signal_path, poll_seconds, timeout_seconds, page, left_form_seconds,
                                         for_captcha=for_captcha, for_blanks=for_blanks)
        finally:
            self._owner_needs = None            # one stop, one alert: a later wait is not this one
            if self._phone is not None:
                try:
                    self._phone.close()
                except Exception as _swallowed:
                    logger.debug("wait_for_signal: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)
                self._phone = None
            try:
                waiting.unlink(missing_ok=True)
            except OSError as _swallowed:
                logger.debug("wait_for_signal: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)
            if page is not None:
                try:
                    provenance.set_agent_busy(page, True)
                except Exception as _swallowed:
                    logger.debug("wait_for_signal: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)

    def _wait_for_signal(
        self, signal_path: Path, poll_seconds: float = 2.0, timeout_seconds: float = 1800.0,
        page: Optional[Page] = None, left_form_seconds: float = 600.0, for_captcha: bool = False,
        for_blanks: bool = False,
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
        except Exception as _swallowed:
            logger.debug("_wait_for_signal: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)
        profile = getattr(self, "_profile", None)
        check_mail = bool(getattr(profile, "check_gmail_for_confirmation", True))
        if page is not None:
            self.raise_window(page)
        waited = 0.0
        away_since: Optional[float] = None
        last_list_reload = 0.0
        last_mail_check: Optional[float] = None
        mail_attempts = 0
        self._confirmation_evidence = ""
        # Stopped for a CAPTCHA: once the user has completed it, carry on by
        # itself. On Schwab's sign-in the user solved the puzzle, the site moved
        # to its next step, and the run went on waiting for an instruction.
        #
        # for_captcha: the run stopped BECAUSE of a CAPTCHA. It is watched for the whole wait, not judged from one
        # look as the wait begins: hCaptcha's picture puzzle redraws itself, one look two seconds after the stop
        # missed it, and Mutual of Enumclaw's run (iCIMS, 29 September) waited for a Continue that never came.
        # It carries on once the CAPTCHA has been seen and then seen gone.
        waiting_on_captcha = for_captcha
        captcha_seen = False
        captcha_gone_polls = 0
        waiting_on_signature = False
        # for_blanks: the run stopped for required boxes only the owner can fill. Once none is blank and the page
        # has stayed the same for a while (the owner has stopped typing), it carries on by itself -- the owner asked
        # why they had to press Continue as well (30 September). Continue still works at any time.
        blanks_checked_at, filled_since, last_look = 0.0, None, None
        captcha_start_path = ""
        captcha_start_passwords = 0
        captcha_page = self._captcha_observation_page(page)
        captcha_top = getattr(captcha_page, "top", None)
        captcha_top_url = getattr(captcha_top, "url", "")
        if page is not None:
            try:
                cur_url = getattr(captcha_page, "url", "") or ""
                captcha_start_path = urlparse(cur_url).path
                captcha_start_passwords = captcha_page.locator("input[type=password]").count()
            except Exception as _swallowed:
                logger.debug("_wait_for_signal: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)
            try:
                captcha_seen = bool(safety.captcha_visible(captcha_page))
            except Exception:
                captcha_seen = False
            waiting_on_captcha = waiting_on_captcha or captcha_seen
            if waiting_on_captcha:
                logger.info("Waiting for you to complete the CAPTCHA; the agent carries on once it is done")
            else:
                # Stopped for a signature only the owner gives: once it is given,
                # carry on without being told.
                try:
                    waiting_on_signature = bool(self.pending_attestations(getattr(page, "top", None) or page))
                except Exception:
                    waiting_on_signature = False
                if waiting_on_signature:
                    logger.info("Waiting for your signature; the agent carries on once you have given it")
        while True:
            # A writer creates the file before writing its answer. Do not consume
            # or delete that intermediate empty file; keep waiting for its text.
            # utf-8-sig accepts the BOM PowerShell may write with a signal.
            try:
                raw = signal_path.read_text(encoding="utf-8-sig").strip().strip("\ufeff")
            except OSError:
                raw = ""
            if raw:
                break
            self._sleep_for_phone(poll_seconds, page)
            waited += poll_seconds
            # Checked first: the page checks below can `continue`, and a skipped
            # timeout left a run on a sign-in page waiting forever.
            if waited >= timeout_seconds:
                raise TimeoutError(f"No review signal received within {timeout_seconds}s")
            if page is not None:
                if page.is_closed():
                    return "browser_closed"
                if waiting_on_signature and not waiting_on_captcha:
                    try:
                        signed = not self.pending_attestations(getattr(page, "top", None) or page)
                    except Exception:
                        signed = False
                    if signed:
                        logger.info("Your signature is given -- carrying on with the application")
                        return "refresh"
                    continue
                if waiting_on_captcha:
                    # A detached/replaced frame temporarily falls back to the outer
                    # document. Its lack of password boxes is not progress evidence.
                    if captcha_top is not None and captcha_top.url == captcha_top_url:
                        try:
                            if captcha_page.frame() == captcha_top.main_frame:
                                captcha_gone_polls = 0
                                continue
                        except Exception:
                            continue
                    try:
                        cur_url = getattr(captcha_page, "url", "") or ""
                        cur_path = urlparse(cur_url).path
                        cur_passwords = captcha_page.locator("input[type=password]").count()
                        page_moved_on = bool(
                            (captcha_start_path and cur_path and cur_path != captcha_start_path)
                            or (captcha_start_passwords > 0 and cur_passwords == 0)
                        )
                    except Exception:
                        page_moved_on = False

                    if page_moved_on:
                        logger.info("The page moved on (%s) -- carrying on with the application", cur_url)
                        page.wait_for_timeout(2_000)
                        return "refresh"

                    try:
                        visible = bool(safety.captcha_visible(captcha_page))
                    except Exception:
                        visible = None      # mid-navigation: look again next poll
                    if visible:
                        captcha_seen, captcha_gone_polls = True, 0
                    elif visible is False and captcha_seen:
                        captcha_gone_polls += 1
                    if captcha_seen and captcha_gone_polls >= 2:
                        logger.info("The CAPTCHA is done -- carrying on with the application")
                        page.wait_for_timeout(2_000)  # let the site's next step finish loading
                        return "refresh"
                    if captcha_seen:
                        continue
                    # Not seen yet: the usual checks below still run, so nothing is carried on on a guess.
                if for_blanks and waited - blanks_checked_at >= 5:
                    blanks_checked_at = waited
                    try:
                        blank = [f for f in form_fields.blank_required(form_fields.inventory(page))
                                 if re.search(r"[A-Za-z]{2}", f.question or f.label or "")]
                        look = page.evaluate("() => [...document.querySelectorAll('input,select,textarea')]"
                                             ".map(e => e.type === 'checkbox' || e.type === 'radio' ? e.checked"
                                             " : e.value).join('|')")
                    except Exception:
                        blank, look = None, None
                    if blank == [] and look is not None:
                        if look != last_look:
                            filled_since = waited      # still changing: the owner may be typing
                        elif filled_since is not None and waited - filled_since >= 15:
                            logger.info("Everything required is filled in -- carrying on with the application")
                            return "refresh"
                    else:
                        filled_since = None
                    last_look = look
                try:
                    on_form = self.find_submit_button(page) is not None
                    if on_form:
                        self._seen_application_form = True
                    # A confirmation page counts even if the form was never
                    # recognised (it needs its message AND no Submit button).
                    if not on_form and self.submission_confirmed(page, job_title):
                        self._confirmation_evidence = "the page confirmed it"
                        return "submitted_by_user"
                    if not getattr(self, "_seen_application_form", False):
                        # Still before the form (a sign-in page, say): nothing
                        # can have been submitted, so no Gmail checks and no
                        # "left the form" timer -- just wait for the user.
                        continue
                    if self.submission_confirmed(page, job_title):
                        self._confirmation_evidence = "the page confirmed it"
                        return "submitted_by_user"
                    if on_form:
                        away_since = None
                    else:
                        away_since = waited if away_since is None else away_since
                        # An applications list may not show a brand-new
                        # submission until reloaded.
                        if re.search(r"/(?:applications|my-applications|dashboard)/?$", urlparse(page.url).path) and waited - last_list_reload >= 60:
                            last_list_reload = waited
                            page.reload(wait_until="domcontentloaded", timeout=30_000)
                        # The site shows nothing conclusive: look for the
                        # employer's confirmation email, every 2 minutes.
                        if check_mail and self._confirmation_email_due(
                                waited - away_since, None if last_mail_check is None else last_mail_check - away_since,
                                mail_attempts):
                            last_mail_check = waited
                            mail_attempts += 1
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
