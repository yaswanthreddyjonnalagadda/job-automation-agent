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
                or current.path != "/mascsr/default/mdf/recruitment/postLogin.html"):
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
        review = steps.get_by_text(re.compile(r"^\s*Review Your Application\s*$", re.I))
        final = steps.get_by_text(re.compile(r"^\s*Self-Attest\s*&\s*Submit\s*$", re.I))
        submit = page.get_by_role("button", name=re.compile(r"^Submit$", re.I))
        if review.count() != 1 or final.count() != 1 or not submit.count() or not submit.first.is_visible():
            return None
        if not review.locator("xpath=ancestor::li[1]").count() or not final.locator("xpath=ancestor::li[1]").count():
            return None
        initial_url = page.url
        documents = []
        restored = False
        try:
            review.click(timeout=4000)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if page.url != initial_url:
                    break
                documents = assistant.attached_document_names(page)
                if documents:
                    break
                page.wait_for_timeout(200)
        except Exception:
            documents = []
        finally:
            if page.url == initial_url:
                try:
                    final.click(timeout=4000)
                    submit.first.wait_for(state="visible", timeout=4000)
                    restored = page.url == initial_url
                except Exception:
                    restored = False
        return documents if restored else []
