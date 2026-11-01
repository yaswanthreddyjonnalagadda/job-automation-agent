"""A page that drew no heading or field is loaded again, once, not read again for ever (f279).

Post Consumer Brands' posting: the agent's own profile drew the header and footer but not the job or its Apply
button, so the plan had no way forward and every re-read saw the same half-drawn page. A real load draws it.
"""
from types import SimpleNamespace

import page_agent
from test_run_recovery_regressions import assistant, browser, page  # noqa: F401


def agent_for(assistant, page, tmp_path):
    return page_agent.PageAgent(
        assistant, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(accept_application_privacy_prompts=False),
        SimpleNamespace(raw_text=""), SimpleNamespace(url=page.url), resume_file=None, job_dir=tmp_path)


def serve(page, drawn_on_load):
    loads = []

    def route(r):
        loads.append(1)
        body = ('<button>Skip to Main Content</button><nav><a href="/">Home</a></nav><p>Equal opportunity employer.</p>' if len(loads) < drawn_on_load
                else '<h1>Engineer</h1><a role="button" href="/apply">Apply</a>')
        r.fulfill(status=200, content_type="text/html", body=body)
    page.route("https://employer.example/**", route)
    page.goto("https://employer.example/job")
    return loads


def press(agent, page):
    agent.read_when_loaded(page)


def test_a_page_with_no_heading_or_field_is_loaded_again_once(assistant, page, tmp_path):
    loads = serve(page, drawn_on_load=2)
    agent = agent_for(assistant, page, tmp_path)
    press(agent, page)
    assert len(loads) == 2
    assert page.locator("h1").count() == 1


def test_a_page_that_stays_empty_is_loaded_again_only_twice(assistant, page, tmp_path):
    loads = serve(page, drawn_on_load=99)
    agent = agent_for(assistant, page, tmp_path)
    for _ in range(4):
        press(agent, page)
    assert len(loads) == 3


def test_a_page_that_drew_a_heading_is_not_loaded_again(assistant, page, tmp_path):
    loads = serve(page, drawn_on_load=1)
    agent = agent_for(assistant, page, tmp_path)
    press(agent, page)
    assert len(loads) == 1


def test_a_site_that_hides_its_job_from_declined_cookies_is_shown_it_once_cookies_are_accepted(assistant, page, tmp_path):
    """f279: Post Consumer Brands' Jibe site drew no job and no Apply button once optional cookies were declined."""
    def route(r):
        declined = "notice_gdpr_prefs=0:" in r.request.headers.get("cookie", "")
        body = ('<button>Skip to Main Content</button><p>Equal opportunity employer.</p>' if declined else
                '<h1>Engineer</h1><a role="button" href="/apply">Apply</a>'
                '<div id="banner"><button onclick="document.cookie=\'notice_gdpr_prefs=0,1,2:;path=/\';'
                'this.parentNode.remove()">Accept All</button></div>')
        r.fulfill(status=200, content_type="text/html", body=body)
    page.context.add_cookies([{"name": "notice_gdpr_prefs", "value": "0:", "url": "https://employer.example/"}])
    page.route("https://employer.example/**", route)
    page.goto("https://employer.example/job")
    agent = agent_for(assistant, page, tmp_path)
    agent.read_when_loaded(page)      # loaded again: still declined
    assert page.locator("h1").count() == 0
    agent.read_when_loaded(page)      # cookie choice cleared, banner accepted
    assert page.locator("h1").count() == 1
