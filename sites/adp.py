"""ADP's posting identifiers and final wizard panels."""
from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from .base import SiteAdapter


class ADPAdapter(SiteAdapter):
    name = "adp"
    hosts = ("workforcenow.adp.com",)

    @classmethod
    def matches(cls, url):
        return urlsplit(url or "").hostname == "workforcenow.adp.com"

    def submission_posting_url(self, page, posting_url, title):
        """Normalize a login redirect only after ADP confirms the requisition."""
        original, current = urlsplit(posting_url), urlsplit(page.url)
        if (not self.matches(posting_url) or not self.matches(page.url)
                or original.scheme != "https" or current.scheme != "https"
                or original.netloc != current.netloc
                or original.path != "/mascsr/default/mdf/recruitment/recruitment.html"
                or current.path not in {"/mascsr/default/mdf/recruitment/postLogin.html",
                                        "/mascsr/applicant/mdf/recruitment/postLogin.html"}):
            return None
        queries = [parse_qs(p.query) for p in (original, current)]

        def one(query, key):
            values = query.get(key, [])
            return values[0] if values and values[0] and len(set(values)) == 1 else ""

        for key in ("cid", "ccId", "jobId"):
            if not one(queries[0], key) or one(queries[0], key) != one(queries[1], key):
                return None
        external_id = one(queries[0], "jobId")
        requisition = one(queries[1], "requisitionId")
        if not requisition or not re.fullmatch(r"[A-Za-z0-9_-]+", external_id):
            return None
        endpoint = (f"https://{original.netloc}/mascsr/default/careercenter/public/events/staffing/"
                    f"v1/job-requisitions/{external_id}?" + urlencode({
                        "cid": one(queries[0], "cid"), "ccId": one(queries[0], "ccId"),
                        "lang": one(queries[0], "lang") or "en_US", "locale": "en_US"}))
        try:
            top = getattr(page, "top", None) or page
            response = top.request.get(endpoint, timeout=15000, max_redirects=0, headers={
                "X-Requested-With": "XMLHttpRequest", "X-Forwarded-Host": original.hostname,
                "Content-Type": "application/json", "locale": "en_US"})
            if not response.ok:
                return None
            metadata = response.json()
            identifiers = {str(item.get("stringValue", ""))
                           for item in metadata.get("customFieldGroup", {}).get("stringFields", [])
                           if item.get("nameCode", {}).get("codeValue") == "ExternalJobID"}
            if (str(metadata.get("itemID", "")) == requisition
                    and identifiers == {external_id}
                    and " ".join(str(metadata.get("requisitionTitle", "")).split()).casefold()
                    == " ".join(title.split()).casefold()):
                return posting_url
        except Exception:
            pass
        return None

    def submission_documents(self, assistant, page):
        """Read the wizard's review panel, then restore its final panel.

        No attachment is inferred from a previous upload attempt or filename cache.
        If the review or final panel cannot be verified, return no evidence.
        """
        if not self.matches(page.url):
            return None
        steps = page.get_by_role("listitem")

        def step(pattern):
            named = page.get_by_role("listitem", name=pattern)
            return named if named.count() == 1 else steps.filter(has_text=pattern)

        def click_step(row):
            actions = row.locator("button:visible, a:visible, [role=button]:visible, [role=link]:visible")
            handles_click = row.evaluate("e => typeof e.onclick === 'function' || e.tabIndex >= 0")
            target = row if handles_click or actions.count() != 1 else actions
            try:
                target.click(timeout=4000)
            except Exception as exc:
                import safety
                if (self._failure_kind(exc) != "covered" or safety.captcha_visible(page)
                        or not target.is_visible() or not target.is_enabled()
                        or not target.evaluate("e => !e.closest('[aria-disabled=true]') && !e.disabled")):
                    raise
                # Only an enabled wizard step, never the final Submit control.
                # Existing containment stays armed; the caller verifies the panel.
                target.evaluate("e => e.click()")

        review = step(re.compile(r"Review\s+Your\s+Application", re.I))
        final = step(re.compile(r"Self[-\s]Attest\s*&\s*Submit", re.I))
        submit = page.get_by_role("button", name=re.compile(r"^Submit$", re.I))
        if review.count() != 1 or final.count() != 1 or not submit.count() or not submit.first.is_visible():
            self._probe_note(assistant, "review", review.count(), "blocked")
            self._probe_note(assistant, "submission", final.count(), "blocked")
            return None
        initial_url = page.url
        documents = []
        restored = False
        try:
            click_step(review)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if page.url != initial_url:
                    break
                documents = assistant.attached_document_names(page)
                if documents:
                    break
                page.wait_for_timeout(200)
        except Exception as exc:
            documents = []
            self._probe_note(assistant, "review", 1, "failed", self._failure_kind(exc))
        finally:
            if page.url == initial_url:
                try:
                    click_step(final)
                    submit.first.wait_for(state="visible", timeout=4000)
                    restored = page.url == initial_url
                except Exception as exc:
                    restored = False
                    self._probe_note(assistant, "submission", 1, "failed", self._failure_kind(exc))
        self._probe_note(assistant, "review", len(documents), "verified" if documents else "unknown")
        self._probe_note(assistant, "submission", 1 if restored else 0, "verified" if restored else "unknown")
        return documents if restored else []

    @staticmethod
    def _probe_note(assistant, stage, count, result, failure_kind=None):
        tracker = getattr(assistant, "tracker", None)
        if tracker is not None and hasattr(tracker, "record_event"):
            try:
                payload = {"action": "navigate", "stage": stage, "count": count, "result": result}
                if failure_kind:
                    payload["failure_kind"] = failure_kind
                tracker.record_event(getattr(assistant, "application_key", ""), "note",
                    "ADP attachment review probe", payload=payload)
            except Exception:
                pass

    @staticmethod
    def _failure_kind(exc):
        text = str(exc).lower()
        for phrase, code in (("not visible", "hidden"), ("intercepts pointer events", "covered"),
                             ("not enabled", "disabled"), ("detached", "detached")):
            if phrase in text:
                return code
        return "timeout" if type(exc).__name__ == "TimeoutError" else "unknown"
