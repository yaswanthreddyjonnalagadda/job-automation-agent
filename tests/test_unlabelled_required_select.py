"""A required select whose question is a heading above it is read, found blank, and answered from the profile (f287).

Bitdeer on Breezy HR, 1 November 2026: 'Which location are you applying for *' is a <select name=cLocation required>
with a placeholder option value="?" and the question in an <h3> beside it, not a <label>. The agent left it empty and
still reported 'every required field is filled and the form shows no errors'; the page said 'Preferred location is
required' under it.
"""
import pytest

import form_fields
from test_run_recovery_regressions import assistant, browser, page  # noqa: F401

HTML = """
<div class="preferred-location"><h3><span>Which location are you applying for</span><span class="required" title="Required">*</span></h3>
<select name="cLocation" required>
  <option value="?"></option><option value="0">San Jose, CA</option><option value="1">Austin, TX</option>
</select>
<div><span class="error" style="display:none">Preferred location is required</span></div></div>
"""


def test_the_required_select_is_seen_as_blank(page):
    page.set_content(HTML)
    blank = form_fields.blank_required(form_fields.inventory(page))
    assert any("location" in (f.question or f.label or f.name or "").lower() for f in blank)


def test_a_placeholder_option_with_a_value_is_still_blank(page, assistant):
    """Angular's 'unknown option' is <option value="?"></option>: its value is not empty but nothing is chosen."""
    page.set_content(HTML)
    report = assistant.find_required_blanks(page)
    assert any("location" in text.lower() for text in report["required_still_blank"])


def test_a_chosen_option_is_not_blank(page, assistant):
    page.set_content(HTML.replace('<option value="1">', '<option value="1" selected>'))
    report = assistant.find_required_blanks(page)
    assert not any("location" in text.lower() for text in report["required_still_blank"])
