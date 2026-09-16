"""
amazon.jobs (account.amazon.jobs) -- Amazon's own application system.

Its screening questions are select2 widgets: a hidden <select> with no id,
driven by a <span role="combobox">, inside a block carrying the question's id
as data-questionid. The generic scan looks for a visible select or an input
with an id, so it found none of them and every question was left blank -- the
form then refused to save.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from .base import SiteAdapter

logger = logging.getLogger(__name__)

_PLACEHOLDER = re.compile(r"^\s*(select an option|select|choose one)?\s*$", re.I)


class AmazonAdapter(SiteAdapter):
    name = "amazon"
    hosts = ("amazon.jobs",)
    confirmation_phrases = (
        "your application has been submitted",
        "thank you for applying to amazon",
        "we have received your application",
    )
    portal_list_patterns = ("/applications", "/applicant/jobs")

    # -- questions ---------------------------------------------------------
    def platform_questions(self, page) -> list[dict]:
        """Every screening question on the page, with the options it offers and
        whatever is chosen already."""
        try:
            return page.evaluate("""() => {
                const out = [];
                for (const block of document.querySelectorAll('[data-questionid]')) {
                    const qid = block.getAttribute('data-questionid');
                    const label = document.getElementById(qid + '-label')
                               || block.querySelector('label:not([for=dropDownValues])');
                    const select = block.querySelector('select');
                    const rendered = block.querySelector('[class*=select2-selection__rendered]');
                    const textbox = block.querySelector('input[type=text], textarea');
                    if (!label || (!select && !textbox)) continue;
                    out.push({
                        qid,
                        question: (label.innerText || '').replace(/\\s+/g, ' ').trim(),
                        options: select ? [...select.options].map(o => o.text.trim()).filter(Boolean) : [],
                        value: select ? (rendered ? rendered.innerText.trim() : '')
                                      : (textbox.value || '').trim(),
                        kind: select ? 'select2' : 'text',
                        required: !!block.querySelector('.required, [aria-required=true]'),
                    });
                }
                return out;
            }""")
        except Exception as exc:
            logger.warning("Could not read Amazon's questions: %s", exc)
            return []

    def answer_platform_question(self, assistant, page, selector: str, answer: str) -> bool:
        """Answers one question by its data-questionid.

        A select2 dropdown is driven through its own widget rather than by
        setting the hidden <select>: the page only learns about a choice made
        the way a person would make it.
        """
        block = page.locator(f'[data-questionid={selector!r}]').first
        try:
            if block.count() == 0:
                return False
            textbox = block.locator("input[type=text], textarea").first
            if block.locator("select").count() == 0:
                textbox.fill(answer)
                textbox.blur()
                return True

            block.locator("[class*=select2-selection]").first.click()
            page.wait_for_timeout(600)
            options = page.locator("li[class*=select2-results__option]:visible")
            texts = [t.strip() for t in options.all_inner_texts()]
            index = assistant._best_option(texts, [answer])
            if index is None:
                page.keyboard.press("Escape")
                assistant.note_ambiguous_choice(self._question_of(page, selector), texts, answer)
                return False
            options.nth(index).click()
            page.wait_for_timeout(400)
            return True
        except Exception as exc:
            logger.warning("Could not answer Amazon question %s: %s", selector[:12], exc)
            return False

    @staticmethod
    def _question_of(page, qid: str) -> str:
        try:
            return page.locator(f"[id={qid + '-label'!r}]").first.inner_text().strip()
        except Exception:
            return qid

    def unanswered_questions(self, page) -> list[dict]:
        return [q for q in self.platform_questions(page) if _PLACEHOLDER.match(q.get("value") or "")]

    # -- uploads -----------------------------------------------------------
    def attachment_is_empty(self, page, kind: str) -> Optional[bool]:
        """Amazon lists an attachment as "Download <name>.pdf" in a panel of
        its own; without this the resume was uploaded again at every step."""
        if kind != "resume":
            return None
        try:
            links = page.locator("a[class*=document-block-title], a[href*='/documents/']")
            for i in range(min(links.count(), 10)):
                if re.search(r"\.(pdf|docx?|rtf|txt)\b", links.nth(i).inner_text(), re.I):
                    return False  # something is attached
        except Exception:
            return None
        return None
