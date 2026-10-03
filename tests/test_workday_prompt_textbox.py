"""Search text is not a committed answer in textbox-shaped Workday prompts."""
from types import SimpleNamespace

import pytest

import config
import page_agent
import safety
from browser_automation import JobApplicationAssistant


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    page = context.new_page()
    yield page
    context.close()


def agent():
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    return page_agent.PageAgent(assistant, SimpleNamespace(), SimpleNamespace(auto_submit=False),
                               config.UserProfile(full_name="Example Candidate"),
                               SimpleNamespace(raw_text=""), SimpleNamespace(url="https://jobs.example.com"))


def widget(marker, offered="Referral network"):
    return f'''<label for="arbitrary-field">Application source *</label>
    <div data-automation-id="multiSelectContainer">
      <div data-automation-id="monikerSearchBox">
        <input id="arbitrary-field" {marker} data-uxi-multiselect-id="picker-1"
          oninput="document.getElementById('choices').hidden=false"
          onkeydown="if(event.key==='Enter')document.body.dataset.enter='yes'">
      </div>
      <span data-automation-id="selectedItem"></span>
      <div id="choices" hidden>
        <div role="option" data-automation-id="promptOption" data-automation-label="{offered}"
          onclick="document.querySelector('[data-automation-id=selectedItem]').textContent=this.textContent;
                   document.getElementById('arbitrary-field').value='';this.parentElement.hidden=true">{offered}</div>
      </div>
    </div>'''


@pytest.mark.parametrize("marker", ['data-uxi-widget-type="selectinput"', 'data-automation-id="searchBox"'])
@pytest.mark.parametrize("action", ["fill", "choose"])
def test_textbox_shaped_prompt_commits_the_matching_option(page, marker, action):
    page.set_content(widget(marker))
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role == "textbox")
    assert a.do(page, page_agent.Answer(control.ref, control.question, action, "Referral network", "profile"), control)
    assert page.locator('[data-automation-id=selectedItem]').inner_text() == "Referral network"
    assert page.locator("input").input_value() == ""
    assert page.evaluate("document.body.dataset.enter") is None


def test_prompt_without_the_requested_option_is_not_filled_with_search_text(page):
    page.set_content(widget('data-uxi-widget-type="selectinput"', offered="Other source"))
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role == "textbox")
    assert not a.do(page, page_agent.Answer(control.ref, control.question, "fill", "Referral network", "profile"), control)
    assert page.locator('[data-automation-id=selectedItem]').inner_text() == ""
    assert page.evaluate("document.body.dataset.enter") is None


def test_plain_search_textbox_is_still_a_text_field(page):
    page.set_content('<label for="x">Application source</label><input id="x" placeholder="Search">')
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role == "textbox")
    assert a.do(page, page_agent.Answer(control.ref, control.question, "fill", "Referral network", "profile"), control)
    assert page.locator("input").input_value() == "Referral network"
