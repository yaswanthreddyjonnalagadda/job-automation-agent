"""Contain autonomous application submission behind a verified gateway."""

from __future__ import annotations

import json
import logging
import secrets
import weakref
from pathlib import Path
from typing import Any

import safety
from playwright.sync_api import Error as PlaywrightError

logger = logging.getLogger(__name__)

_GUARD_NAME = "__jaaSubmissionGuardV0"


# One conservative DOM predicate shared by posting detection and the click firewall.
# Labels or a planner's page_kind alone cannot establish opening vs. submitting.
_APPLICATION_CONTEXT_JS = r"""() => {
    // Cookie managers retain hidden utility controls after their banner closes.
    // Only these recognizable utilities are exempt; hidden applicant inputs,
    // including inputs placed in the same container, still count as state.
    const cookieUtility = input => !input.getClientRects().length && !input.form
        && input.closest('#onetrust-consent-sdk, #onetrust-pc-sdk')
        && (input.type === 'checkbox' || (input.type === 'text'
            && /^cookie list search$/i.test(input.getAttribute('aria-label') || '')));
    const roots = [document];
    for (let i = 0; i < roots.length; i++) {
        const root = roots[i];
        if (root.querySelector('form, textarea, select, [role="textbox"], [role="combobox"], '
            + '[role="checkbox"], [role="radio"], [contenteditable="true"]')) return true;
        if (Array.from(root.querySelectorAll('input')).some(input =>
            !['search', 'button', 'submit', 'image', 'reset'].includes(input.type)
                && !cookieUtility(input))) return true;
        for (const element of root.querySelectorAll('*')) {
            if (element.shadowRoot) roots.push(element.shadowRoot);
        }
    }
    return false;
}"""
_POSTING_APPLY_JS = r"""element => {
    if (!element || element.form || element.closest('form') || element.hasAttribute('form')) return false;
    const labels = [element.innerText, element.value, element.getAttribute('aria-label'),
        element.getAttribute('title')].map(value => String(value || '').replace(/\s+/g, ' ').trim())
        .filter(Boolean);
    if (!labels.length || !labels.every(label => /^apply(?: now| for (?:this|the) job(?: online)?)?$/i.test(label)))
        return false;
    if ((APPLICATION_CONTEXT)()) return false;
    const body = document.body ? document.body.innerText : '';
    if (!/responsibilities|qualifications|requirements|job description|about the (role|job|position)|what you('ll| will) do|who you are|job (id|number|requisition)|posted/i.test(body)) return false;
    if (/review (your|and submit)|submit (your|this) application|confirm (your|this) application|application (review|summary)|electronic signature|e-signature/i.test(body)) return false;
    return Boolean(document.querySelector('h1, h2, [role="heading"]'));
}""".replace('APPLICATION_CONTEXT', _APPLICATION_CONTEXT_JS)


def is_posting_apply(control: Any) -> bool:
    """Require positive posting evidence and absence of application DOM state."""
    try:
        return bool(control.evaluate(_POSTING_APPLY_JS))
    except (PlaywrightError, AttributeError):
        return False


def _guard_script(capability: str) -> str:
    return r"""(() => {
      // A document.open()-class replacement (what Playwright's page.set_content() uses, and
      // what some legacy document.write()-based popup population does) removes every listener
      // this script attaches with addEventListener -- confirmed directly, not assumed -- while
      // leaving window.__jaaSubmissionGuardV0 itself and everything on it (including function
      // references) intact. Object/property presence therefore proved insufficient evidence
      // that containment was actually installed (the owner's hardening finding, 7 October 2026).
      // So a second run of this script, when the object already exists, does not just bail out:
      // it re-attaches listeners through the SAME persisted function references, which
      // addEventListener treats as a no-op when already attached and a repair when not --
      // idempotent either way, with no duplicate listeners or duplicate denial events.
      if (window.__jaaSubmissionGuardV0) {
        window.__jaaSubmissionGuardV0.__ensureListeners();
        return;
      }
      const capability = CAPABILITY;
      const submitLabel = new RegExp(SUBMIT_PATTERN, "i");
      const safeUtilityLabel = new RegExp(SAFE_UTILITY_PATTERN, "i");
      const applicationContext = APPLICATION_CONTEXT;
      const postingApply = POSTING_APPLY;
      const state = {active: false, authorization: null, denials: [], sequence: 0,
        applicationSeen: applicationContext()};
      const selector = 'button, input[type="submit"], input[type="image"], [role="button"], a';
      const normalize = value => String(value || "").replace(/\s+/g, " ").trim();
      const labels = element => [
        element.getAttribute("aria-label"),
        element.innerText,
        element.value,
        element.getAttribute("title")
      ].map(normalize).filter(Boolean);
      const structuralSubmitter = element =>
        element instanceof HTMLButtonElement
          ? Boolean(element.form)
            && (element.type.toLowerCase() === "submit" || !element.hasAttribute("type"))
          : element instanceof HTMLInputElement && Boolean(element.form)
            && ["submit", "image"].includes(element.type.toLowerCase());
      const ordinaryNavigation = element =>
        labels(element).some(label => /^(next|continue)(?:\b|$)/i.test(label));
      // Default-deny for a labelled, form-associated control whose own label is neither a known
      // submit synonym nor a known non-final (add/remove/edit/cancel/back/save-for-later/...)
      // word, when it is the only such candidate left in its form: a button[type=button] with a
      // custom onclick handler ("Confirm & Send", "Finalize", a localized equivalent) has no
      // structural marker at all, so no closed vocabulary can ever be complete (the adversarial
      // review's Finding 1, 7 October 2026). An icon-only or otherwise unlabeled control is left
      // alone -- unclassifiable either way, same as before this change -- and so is any control
      // that is not the sole remaining action in its form, since "Add"/"Remove" clutter alongside
      // a real submit button is the common case this must not misfire on.
      // A dropdown/disclosure trigger (Ant Design-style detached dropdowns, a custom combobox,
      // any "opens something" button) is never a submit control; aria-haspopup/aria-expanded/
      // aria-controls are the standard accessible markers for that pattern across this project's
      // own site adapters (interaction.resolve_ant_dropdown()) and are excluded here for the same
      // reason the safe-utility words are -- a BambooHR-style "State -Select-" opener beside a
      // file-chooser button was otherwise the only remaining "candidate" in its form.
      const opensSomething = element =>
        element.hasAttribute("aria-haspopup") || element.hasAttribute("aria-expanded")
          || element.hasAttribute("aria-controls");
      const unclassifiedFinalCandidate = element => {
        if (!(element instanceof HTMLButtonElement)
            && !(element instanceof Element && element.getAttribute("role") === "button")) return false;
        if (!element.form || element.disabled) return false;
        if (opensSomething(element)) return false;
        const elementLabels = labels(element);
        if (!elementLabels.length) return false;
        if (ordinaryNavigation(element)) return false;
        if (elementLabels.some(label => safeUtilityLabel.test(label))) return false;
        return true;
      };
      const soleRemainingAction = candidate => {
        if (!unclassifiedFinalCandidate(candidate)) return false;
        return !Array.from(candidate.form.elements)
          .some(el => el !== candidate && unclassifiedFinalCandidate(el));
      };
      const finalControl = element => {
        if (!(element instanceof Element)) return null;
        const candidate = element.closest(selector);
        if (!candidate) return null;
        state.applicationSeen ||= applicationContext();
        if (!state.applicationSeen && postingApply(candidate)) return null;
        if (labels(candidate).some(label => submitLabel.test(label))) return candidate;
        if (structuralSubmitter(candidate) && !ordinaryNavigation(candidate)) return candidate;
        return soleRemainingAction(candidate) ? candidate : null;
      };
      const firstSubmitter = form => Array.from(form.elements).find(structuralSubmitter) || null;
      const finalForm = (form, submitter) => submitter
        ? Boolean(finalControl(submitter))
        : Array.from(form.elements).some(element => Boolean(finalControl(element)));
      const recordDenial = (kind, element) => {
        const item = {
          sequence: ++state.sequence,
          kind,
          label: element ? labels(element).join(" ").slice(0, 120) : "application form"
        };
        state.denials.push(item);
        console.warn("SUBMISSION_GUARD_V0_DENIED " + JSON.stringify(item));
      };
      const denyEvent = (event, kind, element) => {
        recordDenial(kind, element);
        event.preventDefault();
        event.stopImmediatePropagation();
      };
      const authorizedClick = element => {
        const authorization = state.authorization;
        if (!authorization || authorization.capability !== capability || authorization.element !== element) {
          return false;
        }
        authorization.clickAllowed = true;
        setTimeout(() => {
          if (state.authorization === authorization) state.authorization = null;
        }, 0);
        return true;
      };
      const authorizedForm = form => {
        const authorization = state.authorization;
        return Boolean(authorization && authorization.capability === capability
          && authorization.form === form && authorization.clickAllowed);
      };

      // Named, stable function references -- not inline closures passed directly to
      // addEventListener -- so that re-attaching them later (ensureListeners, below) is the
      // SAME listener as far as addEventListener is concerned, and is therefore deduplicated
      // automatically rather than ever being registered twice.
      const onClick = event => {
        if (!state.active) return;
        const path = typeof event.composedPath === "function" ? event.composedPath() : [event.target];
        let candidate = null;
        for (const target of path) {
          candidate = finalControl(target);
          if (candidate) break;
        }
        if (!candidate || authorizedClick(candidate)) return;
        denyEvent(event, "click", candidate);
      };
      const onKeydown = event => {
        if (!state.active || event.key !== "Enter") return;
        const target = event.target;
        if (!(target instanceof HTMLInputElement) || ["button", "submit", "image", "reset", "checkbox",
          "radio", "file", "hidden"].includes((target.type || "text").toLowerCase())) return;
        const form = target.closest("form");
        if (!form) return;
        const submitter = firstSubmitter(form);
        if (!finalControl(submitter)) return;
        if (authorizedForm(form)) return;
        denyEvent(event, "enter", submitter);
      };
      const onSubmit = event => {
        if (!state.active) return;
        const form = event.target;
        if (!(form instanceof HTMLFormElement) || !finalForm(form, event.submitter)) return;
        if (authorizedForm(form)) {
          state.authorization = null;
          return;
        }
        denyEvent(event, "form_submit", event.submitter || firstSubmitter(form));
      };
      // Idempotent by construction: addEventListener silently discards an attempt to register
      // the same function reference with the same capture flag twice, so calling this on every
      // activate() -- which happens on every PageAgent loop iteration already -- is always safe,
      // whether listeners are already live (no-op) or were silently stripped (repaired).
      const ensureListeners = () => {
        window.addEventListener("click", onClick, true);
        window.addEventListener("keydown", onKeydown, true);
        window.addEventListener("submit", onSubmit, true);
      };
      ensureListeners();

      const nativeSubmit = HTMLFormElement.prototype.submit;
      HTMLFormElement.prototype.submit = function() {
        if (state.active && finalForm(this) && !authorizedForm(this)) {
          recordDenial("javascript_form_submit", firstSubmitter(this));
          return;
        }
        return nativeSubmit.call(this);
      };

      const nativeRequestSubmit = HTMLFormElement.prototype.requestSubmit;
      if (nativeRequestSubmit) {
        HTMLFormElement.prototype.requestSubmit = function(submitter) {
          if (state.active && finalForm(this, submitter) && !authorizedForm(this)) {
            recordDenial("javascript_request_submit", submitter || firstSubmitter(this));
            return;
          }
          return nativeRequestSubmit.call(this, submitter);
        };
      }

      const api = Object.freeze({
        activate(token) {
          if (token === capability) {
            state.active = true;
            state.applicationSeen ||= applicationContext();
            ensureListeners();
          }
        },
        deactivate(token) {
          if (token === capability) state.active = false;
        },
        __ensureListeners: ensureListeners,
        authorize(element, token) {
          if (token !== capability || !state.active || !finalControl(element)) return false;
          state.authorization = {element, form: element.form || element.closest("form"), capability};
          return true;
        },
        clearAuthorization(token) {
          if (token === capability) state.authorization = null;
        },
        denialCount() {
          return state.denials.length;
        },
        denials() {
          return state.denials.slice();
        }
      });
      Object.defineProperty(window, "__jaaSubmissionGuardV0", {
        value: api, configurable: false, writable: false
      });
    })()""".replace("CAPABILITY", json.dumps(capability)).replace(
        "SUBMIT_PATTERN", json.dumps(safety.SUBMIT_LABEL_RE.pattern)
    ).replace(
        "SAFE_UTILITY_PATTERN", json.dumps(safety.SAFE_UTILITY_LABEL_RE.pattern)
    ).replace("POSTING_APPLY", _POSTING_APPLY_JS).replace(
        "APPLICATION_CONTEXT", _APPLICATION_CONTEXT_JS
    )


class SubmissionGuardV0:
    """Browser-side deny-by-default guard with a one-use verified submit gateway."""

    def __init__(self, context: Any):
        self.context = context
        self._capability = secrets.token_urlsafe(32)
        self._script = _guard_script(self._capability)
        self._active_pages: weakref.WeakSet = weakref.WeakSet()
        self._tracker: Any = None
        self._dedup_key = ""
        context.add_init_script(script=self._script)
        for page in context.pages:
            for frame in page.frames:
                try:
                    frame.evaluate(self._script)
                except PlaywrightError:
                    logger.debug("Submission guard could not initialize a closing frame")
        # Containment must not depend on every caller remembering to call activate() on a
        # specific page: a popup or new tab (window.open(), target="_blank" -- common on Taleo,
        # BrassRing, and Workday external links) previously stayed at its script-default
        # state.active=false until something explicitly activated it (the adversarial review's
        # Finding 3, 7 October 2026). This context-wide hook activates every page already open
        # when the guard is constructed, and every page opened in this context afterward,
        # automatically -- "already exists" and "created afterward" are both covered, with no
        # separate per-page wiring required anywhere else.
        for page in context.pages:
            try:
                self.activate(page)
            except PlaywrightError:
                logger.debug("Submission guard could not activate an existing page at construction")
        context.on("page", self._activate_new_page)

    def _activate_new_page(self, page: Any) -> None:
        try:
            self.activate(page)
        except PlaywrightError:
            logger.debug("Submission guard could not activate a newly opened page/tab/popup")

    def bind_application(self, tracker: Any, dedup_key: str) -> None:
        self._tracker = tracker
        self._dedup_key = dedup_key

    def _on_console(self, message: Any) -> None:
        text = message.text
        prefix = "SUBMISSION_GUARD_V0_DENIED "
        if not text.startswith(prefix) or self._tracker is None or not self._dedup_key:
            return
        try:
            denial = json.loads(text[len(prefix):])
            self._record_event(
                self._tracker, self._dedup_key, "SUBMISSION_BLOCKED",
                {"reason": str(denial.get("kind", "browser_guard"))[:40]},
            )
        except (TypeError, ValueError):
            logger.warning("Could not parse browser submission-denial event")

    def _activate_frame(self, frame: Any) -> None:
        """Arm this frame's guard and repair its listeners if a document.open()-class
        replacement stripped them. `activate()` itself now re-attaches listeners on every call
        (see `_guard_script`'s `ensureListeners`); the fallback below only covers the rarer case
        where the guard object is missing from this frame entirely (never injected in the first
        place), by falling back to a full (re)injection rather than assuming object presence."""
        try:
            frame.evaluate(
                f"token => window.{_GUARD_NAME}.activate(token)",
                self._capability,
            )
        except PlaywrightError:
            frame.evaluate(self._script)
            frame.evaluate(
                f"token => window.{_GUARD_NAME}.activate(token)",
                self._capability,
            )

    def activate(self, page: Any) -> None:
        self._active_pages.add(page)
        if not getattr(page, "_jaa_submission_guard_console_listener", False):
            page.on("console", self._on_console)
            page._jaa_submission_guard_console_listener = True
        if not getattr(page, "_jaa_submission_guard_listeners", False):
            page.on("frameattached", lambda frame: self._activate_attached(page, frame))
            page.on("framenavigated", lambda frame: self._activate_navigated(page, frame))
            page._jaa_submission_guard_listeners = True
        for frame in page.frames:
            self._activate_frame(frame)

    def _activate_attached(self, page: Any, frame: Any) -> None:
        if page in self._active_pages:
            try:
                self._activate_frame(frame)
            except PlaywrightError:
                logger.debug("Submission guard could not activate a closing frame")

    def _activate_navigated(self, page: Any, frame: Any) -> None:
        if page in self._active_pages:
            try:
                self._activate_frame(frame)
            except PlaywrightError:
                logger.debug("Submission guard could not activate a closing frame")

    def deactivate(self, page: Any) -> None:
        self._active_pages.discard(page)
        for frame in page.frames:
            frame.evaluate(
                f"token => window.{_GUARD_NAME}.deactivate(token)",
                self._capability,
            )

    @staticmethod
    def _frame_denials(frame: Any) -> list[dict]:
        return frame.evaluate(
            f"() => window.{_GUARD_NAME} ? window.{_GUARD_NAME}.denials() : []"
        )

    def denials(self, page: Any) -> list[dict]:
        return [denial for frame in page.frames for denial in self._frame_denials(frame)]

    def denial_count(self, page: Any) -> int:
        return sum(len(self._frame_denials(frame)) for frame in page.frames)

    @staticmethod
    def click_denial_count(locator: Any) -> int:
        return locator.evaluate(
            f"element => window.{_GUARD_NAME} ? window.{_GUARD_NAME}.denialCount() : -1"
        )

    @staticmethod
    def click_was_denied(locator: Any, before: int | None) -> bool:
        if before is None or before < 0:
            return False
        try:
            return SubmissionGuardV0.click_denial_count(locator) > before
        except Exception as exc:
            logger.warning("SUBMISSION_GUARD_V0: could not verify click outcome; stopping fallback: %s",
                           str(exc).splitlines()[0][:120])
            return True

    @staticmethod
    def _valid_authorization(decision: Any) -> bool:
        if type(decision) is not safety.AutoSubmitDecision:
            return False
        if decision.eligible is not True or not isinstance(decision.reasons, list) or decision.reasons:
            return False
        evidence = decision.evidence_paths
        if not isinstance(evidence, dict) or not all(
            isinstance(evidence.get(key), str) and Path(evidence[key]).is_file()
            for key in ("screenshot", "html")
        ):
            return False
        if not isinstance(decision.field_comparisons, list) or not decision.field_comparisons \
                or any(type(item) is not safety.FieldComparison for item in decision.field_comparisons):
            return False
        return all(
            not comparison.required or (bool(comparison.approved) and comparison.matches is True)
            for comparison in decision.field_comparisons
        )

    @staticmethod
    def _record_event(tracker: Any, dedup_key: str, kind: str, payload: dict | None = None) -> None:
        try:
            tracker.record_submission_safety_event(dedup_key, kind, payload)
        except Exception:
            logger.exception("%s could not be durably recorded for application %s", kind, dedup_key[:12])

    @staticmethod
    def _mark_uncertain(tracker: Any, dedup_key: str) -> None:
        try:
            if tracker.get_submission_effect_state(dedup_key) == "DISPATCHED":
                tracker.finish_submission_effect(dedup_key, "UNCERTAIN")
        except Exception:
            logger.exception("SUBMISSION_UNCERTAIN could not be durably recorded for application %s",
                             dedup_key[:12])

    def submit_verified(
        self,
        page: Any,
        locator: Any,
        decision: Any,
        tracker: Any = None,
        dedup_key: str = "",
        application_tracker: Any = None,
        application_key: str = "",
        aliases: tuple[str, ...] | list[str] = (),
    ) -> bool:
        """Perform one final click only after a structured verified decision."""
        if not self._valid_authorization(decision):
            logger.error("SUBMISSION_GUARD_V0_DENIED: verified authorization is missing or invalid")
            if tracker is not None and dedup_key:
                self._record_event(tracker, dedup_key, "SUBMISSION_BLOCKED", {"reason": "invalid_authorization"})
            return False
        if tracker is None or not dedup_key or not callable(
            getattr(tracker, "begin_submission_dispatch", None)
        ):
            logger.error("SUBMISSION_STORAGE_FAILURE: durable application identity/store unavailable")
            return False
        application_tracker = application_tracker or tracker
        if not callable(getattr(application_tracker, "get", None)):
            logger.error("SUBMISSION_BLOCKED: persisted application record cannot be revalidated")
            self._record_event(tracker, dedup_key, "SUBMISSION_BLOCKED",
                               {"reason": "application_record_unavailable"})
            return False
        try:
            application = application_tracker.get(application_key or dedup_key)
        except Exception:
            logger.exception("SUBMISSION_STORAGE_FAILURE: application record read failed")
            self._record_event(tracker, dedup_key, "SUBMISSION_STORAGE_FAILURE",
                               {"reason": "application_record_read_failed"})
            return False
        if application is None or not hasattr(application, "status"):
            logger.error("SUBMISSION_BLOCKED: application record is missing or invalid")
            self._record_event(tracker, dedup_key, "SUBMISSION_BLOCKED",
                               {"reason": "application_record_missing"})
            return False
        if application.status == "submitted":
            logger.error("SUBMISSION_REPLAY_BLOCKED: application is already submitted")
            self._record_event(tracker, dedup_key, "SUBMISSION_REPLAY_BLOCKED",
                               {"state": "application_submitted"})
            return False
        self.bind_application(tracker, dedup_key)
        self.activate(page)
        before = self.denial_count(page)
        dispatched = False
        try:
            tracker.begin_submission_dispatch(dedup_key, aliases)
            dispatched = True
            authorized = locator.evaluate(
                f"(element, token) => window.{_GUARD_NAME}.authorize(element, token)",
                self._capability,
            )
            if not authorized:
                logger.error("SUBMISSION_GUARD_V0_DENIED: the target is not an authorized submit control")
                self._mark_uncertain(tracker, dedup_key)
                return False
            locator.click(timeout=10_000)
            after = self.denial_count(page)
            if after != before:
                logger.error("SUBMISSION_GUARD_V0_DENIED: browser interception rejected the submit click")
                self._mark_uncertain(tracker, dedup_key)
                return False
            return True
        except PlaywrightError as exc:
            logger.error("SUBMISSION_GUARD_V0_DENIED: verified submit click failed: %s", str(exc).splitlines()[0][:120])
            if dispatched:
                self._mark_uncertain(tracker, dedup_key)
            return False
        except Exception:
            if not dispatched:
                try:
                    current_state = tracker.get_submission_effect_state(dedup_key)
                except Exception:
                    current_state = None
                if current_state in {"DISPATCHED", "CONFIRMED", "UNCERTAIN"}:
                    self._record_event(
                        tracker, dedup_key, "SUBMISSION_REPLAY_BLOCKED",
                        {"reason": "durable_state_conflict", "state": current_state},
                    )
                    logger.error("SUBMISSION_REPLAY_BLOCKED: durable state is %s", current_state)
                else:
                    self._record_event(tracker, dedup_key, "SUBMISSION_STORAGE_FAILURE",
                                       {"reason": "dispatch_record_failed"})
            else:
                self._mark_uncertain(tracker, dedup_key)
            logger.exception("SUBMISSION_BLOCKED: verified submit gateway failed closed")
            return False
        finally:
            try:
                locator.evaluate(
                    f"(element, token) => window.{_GUARD_NAME}.clearAuthorization(token)",
                    self._capability,
                )
            except PlaywrightError:
                pass


class SubmissionProbe:
    """Read-only reconciliation view; it exposes no submit or interaction methods."""

    @staticmethod
    def inspect(page: Any, tracker: Any, dedup_key: str, application_tracker: Any = None) -> dict:
        state = tracker.get_submission_effect_state(dedup_key)
        application = application_tracker.get(dedup_key) if application_tracker is not None else None
        body = page.locator("body").inner_text(timeout=5_000).lower()
        confirmation_phrases = (
            "thank you for applying", "application submitted", "thanks for applying",
            "we have received your application", "your application has been submitted",
            "submission received", "application received", "application has been sent",
            "application was sent", "applied on", "date applied", "submitted on",
        )
        confirmation_text = next((phrase for phrase in confirmation_phrases if phrase in body), "")
        return {
            "state": state,
            "application_status": getattr(application, "status", None),
            "url": page.url,
            "title": page.title(),
            "confirmation_text": confirmation_text,
            "application_id": page.evaluate("""() => {
              const element = document.querySelector(
                '[data-application-id], [data-applicationid], [data-testid="application-id"]'
              );
              return element && (element.getAttribute('data-application-id')
                || element.getAttribute('data-applicationid') || element.textContent.trim());
            }"""),
            "safety_events": tracker.submission_safety_events(dedup_key),
        }
