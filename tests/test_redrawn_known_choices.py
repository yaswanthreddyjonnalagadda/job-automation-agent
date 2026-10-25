"""Known answers survive a dropdown replacing the remaining form controls."""
from dataclasses import replace

import pytest

import page_agent
from test_workday_prompt_textbox import agent, browser, page  # noqa: F401

FORM = """<main id="form"></main><script>
const answers = {};
const fields = [
  ['gender', 'Gender', ['Male','Female']],
  ['race', 'What is your race/ethnicity?', ['Asian (Not Hispanic or Latino)', 'Hispanic or Latino']],
  ['veteran', 'Veteran', ['Not A Veteran','Veteran']]
];
function render() {
  const root = document.querySelector('#form');
  root.replaceChildren();
  for (const [key, question, options] of fields) {
    const group = document.createElement('div');
    const button = document.createElement('button');
    button.type='button'; button.textContent=answers[key] || 'Select One';
    button.setAttribute('aria-label', question+' '+button.textContent);
    group.onclick = () => {
      const list = document.createElement('div'); list.setAttribute('role','listbox');
      for (const choice of options) {
        const row = document.createElement('div'); row.setAttribute('role','option');
        row.textContent=choice;
        row.onclick=(event) => {event.stopPropagation(); answers[key]=choice; render();};
        list.append(row);
      }
      group.append(list);
    };
    group.append(button); root.append(group);
  }
}
document.addEventListener('keydown', e => {
  if(e.key==='Escape')document.querySelectorAll('[role=listbox]').forEach(e=>e.remove());
});
render();
</script>"""


def test_known_choices_are_resolved_again_after_each_redraw(page, monkeypatch):
    page.set_default_timeout(1_000)
    page.set_content(FORM)
    a = agent()
    a.profile = replace(a.profile, gender='Male', ethnicity='South Asian', veteran_status='Not A Veteran')
    a._profile_answer_library = {}
    a._current_host = 'jobs.example.invalid'
    # Fail immediately on a stale click rather than waiting through browser fallbacks.
    original = a.assistant._click_resiliently
    def click(locator, **kwargs):
        if locator.count() != 1:
            pytest.fail('stale dropdown reference reached the click fallbacks')
        return original(locator, **kwargs)
    monkeypatch.setattr(a.assistant, '_click_resiliently', click)
    controls = page_agent.parse_snapshot(a.snapshot(page))
    filled, unanswered = a.answer_what_is_known(page, controls, set())
    assert unanswered == []
    assert filled == 3
    assert page.evaluate('answers') == {
        'gender':'Male', 'race':'Asian (Not Hispanic or Latino)', 'veteran':'Not A Veteran'
    }
