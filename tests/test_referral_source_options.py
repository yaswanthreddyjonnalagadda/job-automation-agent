"""'How did you hear about us?' -- the owner's usual answer is a brand ('LinkedIn'); a portal's list offers it
under its own wording ('LinkedIn corporate page').

Copart's Workday list is nested: seven categories, and inside 'Job Sites' the leaves Career Builder ... Indeed ...
'LinkedIn corporate page' ... Monster. No leaf is called 'LinkedIn', so the strict matcher refused to choose, the
required field stayed empty and the form would not go on, on four runs in a row (f150). The brand now reaches the
one option that begins with it, and still never guesses between two.
"""
import json

import pytest

import option_match
import page_agent
from test_workday_prompt_textbox import agent, browser, page  # noqa: F401


COPART = {
    "Campus Campaign": ["College", "Handshake", "University"],
    "Career Websites": ["Corporate Website"],
    "Employee Referral": ["Connection in the company"],
    "Job Sites": ["Career Builder", "Duunitori", "Facebook corporate page", "Find a Job", "Glassdoor", "Indeed",
                  "Jobly", "LinkedIn corporate page", "Monster", "Naukri", "ZipRecruiter"],
    "Other": ["Contacted by Copart UK", "Customer/Visited Copart UK"],
    "Social Referral": ["Saw on personal Facebook feed", "Saw on personal LinkedIn feed"],
}


def test_linkedin_reaches_the_one_linkedin_job_site_option():
    leaves = COPART["Job Sites"]
    hit = option_match.best_option(leaves, "LinkedIn")
    assert hit is not None and leaves[hit] == "LinkedIn corporate page"


def test_a_brand_never_chooses_between_two_options_that_begin_with_it():
    assert option_match.best_option(["LinkedIn corporate page", "LinkedIn Jobs"], "LinkedIn") is None


def test_a_feed_post_that_only_contains_the_brand_is_not_the_job_site():
    assert option_match.best_option(COPART["Social Referral"], "LinkedIn") is None


def test_an_exact_option_still_wins_over_a_longer_one():
    options = ["LinkedIn corporate page", "LinkedIn"]
    assert options[option_match.best_option(options, "LinkedIn")] == "LinkedIn"


def test_every_job_site_brand_in_the_data_reaches_its_one_descriptor_option():
    groups = json.loads(option_match.EQUIVALENTS_FILE.read_text(encoding="utf-8"))["referral_source"]
    assert groups, "the referral-source brands live in reference/answer_equivalents.json"
    for group in groups:
        brand = group[0]
        options = [f"{brand} corporate page", "Saw on personal feed", "Other"]
        assert option_match.best_option(options, brand) == 0, brand


def tree_widget(page):
    """Copart's nested list: categories (type 2), their leaves (type 1) and a Back control, as probed."""
    page.set_content('''<label for="src">How Did You Hear About Us? *</label>
    <div data-automation-id="multiSelectContainer">
      <div data-automation-id="monikerSearchBox">
        <input id="src" data-uxi-widget-type="selectinput" data-uxi-multiselect-id="picker-1">
      </div>
      <span data-automation-id="selectedItem"></span>
      <div id="choices" data-automation-id="responsiveMonikerPrompt" data-associated-widget="picker-1" hidden></div>
    </div>''')
    page.evaluate("""tree => {
      const input = document.getElementById('src'), choices = document.getElementById('choices');
      const chip = document.querySelector('[data-automation-id=selectedItem]');
      const root = () => {
        choices.innerHTML = '';
        for (const name of Object.keys(tree)) {
          const wrap = document.createElement('div'); wrap.setAttribute('data-uxi-multiselectlistitem-type', '2');
          const row = document.createElement('div'); row.setAttribute('data-automation-id', 'promptOption');
          row.setAttribute('data-automation-label', name); row.textContent = name;
          row.onclick = () => inside(name); wrap.appendChild(row); choices.appendChild(wrap);
        }
      };
      const inside = name => {
        choices.innerHTML = '<span data-automation-id="backButton"></span>';
        choices.querySelector('[data-automation-id=backButton]').onclick = root;
        for (const leaf of tree[name]) {
          const wrap = document.createElement('div'); wrap.setAttribute('data-uxi-multiselectlistitem-type', '1');
          const row = document.createElement('div'); row.setAttribute('data-automation-id', 'promptOption');
          row.setAttribute('data-automation-label', leaf); row.textContent = leaf;
          row.onclick = () => { chip.textContent = leaf; input.value = ''; choices.hidden = true; };
          wrap.appendChild(row); choices.appendChild(wrap);
        }
      };
      input.addEventListener('click', () => { choices.hidden = false; root(); });
      input.addEventListener('input', () => { if (choices.hidden) { choices.hidden = false; root(); } });
    }""", COPART)


def answer_with(page, value):
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role == "textbox")
    return a.do(page, page_agent.Answer(control.ref, control.question, "fill", value, "profile"), control)


def test_the_agent_walks_coparts_categories_and_chooses_the_linkedin_job_site(page):
    tree_widget(page)
    assert answer_with(page, "LinkedIn")
    assert page.locator("[data-automation-id=selectedItem]").inner_text() == "LinkedIn corporate page"


def test_the_agent_leaves_a_source_the_list_does_not_offer_unchosen(page):
    tree_widget(page)
    assert not answer_with(page, "Billboard")
    assert page.locator("[data-automation-id=selectedItem]").inner_text() == ""
