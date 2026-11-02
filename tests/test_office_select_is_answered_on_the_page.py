"""The office select on a Breezy-style form is chosen on the page, not only by the answer engine (f287)."""
from types import SimpleNamespace

import config
import page_agent
from test_run_recovery_regressions import assistant, browser, page  # noqa: F401

HTML = """
<div class="preferred-location"><h3><span>Which location are you applying for</span><span class="required">*</span></h3>
<select name="cLocation" required>
  <option value="?"></option><option value="0">San Jose, CA</option><option value="1">Austin, TX</option>
</select></div>
"""


def test_the_one_fitting_office_is_selected(assistant, page, tmp_path):
    page.set_content(HTML)
    profile = config.UserProfile(available_work_locations="Texas, Virginia")
    agent = page_agent.PageAgent(assistant, SimpleNamespace(), SimpleNamespace(), profile, SimpleNamespace(raw_text=""),
                                 SimpleNamespace(title="Engineer", company="Bitdeer", url=page.url), resume_file=None,
                                 job_dir=tmp_path)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    agent.answer_what_is_known(page, controls, ["Which location are you applying for"])
    assert page.eval_on_selector("select", "s => s.selectedOptions[0].text") == "Austin, TX"


def test_an_unnamed_select_does_not_adopt_the_heading_above_it_as_its_answer():
    """f287: with no accessible name, the question's own heading was read as the value the select already held."""
    snap = ('- generic [ref=e1]:\n  - heading "Which location are you applying for*" [level=3] [ref=e3]\n'
            '  - combobox [ref=e4]:\n    - option [selected]\n    - option "San Jose, CA"\n    - option "Austin, TX"\n')
    (control,) = [c for c in page_agent.parse_snapshot(snap) if c.role == "combobox"]
    assert control.answer == ""
    assert control.question == "Which location are you applying for*"


def test_a_value_shown_beside_a_named_select_is_still_its_answer():
    snap = '- generic [ref=e1]:\n  - combobox "Country" [ref=e4]:\n  - text: Canada\n'
    snap = '- generic [ref=e1]:\n  - text: Canada\n  - combobox "Country" [ref=e4]\n'
    controls = [c for c in page_agent.parse_snapshot(snap) if c.role == "combobox"]
    assert controls and controls[0].name == "Country"
