"""Long Element UI menus must use exact offered labels, including offscreen rows."""
from types import SimpleNamespace
import pytest
from page_agent import PageAgent, Control
from browser_automation import JobApplicationAssistant
import config

@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        yield page
        browser.close()

@pytest.mark.parametrize("value,offered", [("Virginia", "Virginia"), ("United States", "United States"), ("Virginia", "West Virginia")])
def test_element_choice_beyond_first_eighty_rows(page, value, offered):
    rows = ''.join('<li class="el-select-dropdown__item">Choice %s</li>' % i for i in range(200))
    page.set_content('<div class="el-select" onclick="document.querySelector(\'.el-select-dropdown\').style.display=\'block\'">'
        '<input id="choice" readonly aria-label="Select"></div>'
        '<ul class="el-select-dropdown" style="display:none;max-height:100px;overflow:auto" '
        'onclick="if(event.target.tagName===\'LI\'){document.querySelector(\'#choice\').value=event.target.textContent;this.style.display=\'none\'}">'
        + rows + '<li class="el-select-dropdown__item">' + offered + '</li></ul>')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    agent = PageAgent(assistant, None, SimpleNamespace(), config.UserProfile(), None,
                     SimpleNamespace(title="Engineer", company="Example", url="https://example.com"))
    agent.locate = lambda p, ref: page.locator('#choice')
    agent.snapshot = lambda p: ''
    agent._wait_for_choices = lambda *a, **kw: []
    agent._dump_open_menu = lambda *a: None
    matched = agent._choose_exact(page, Control('choice', 'textbox', name='Select'), value)
    assert matched is (value == offered)
    assert page.locator('#choice').input_value() == (offered if value == offered else '')
