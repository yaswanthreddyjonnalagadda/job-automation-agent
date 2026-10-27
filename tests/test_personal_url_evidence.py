from types import SimpleNamespace
import pytest
from page_agent import PageAgent, Answer, Control


def agent(resume='', known=''):
    a=PageAgent.__new__(PageAgent)
    a.resume=SimpleNamespace(raw_text=resume)
    a.known_answer=lambda control: (known, 'owner_earlier_answer' if known else '')
    return a


@pytest.mark.parametrize('label,url',[
    ('Github Profile*','https://github.com'),
    ('LinkedIn Profile','https://linkedin.com/in/guessed-person'),
    ('Portfolio URL','https://guessed-person.example'),
])
def test_personal_url_requires_evidence_regardless_of_claimed_source(label,url):
    a=agent('Network engineer. Uses GitHub Actions.')
    assert not a._supported_web_address(Control('a','textbox',label),url)


@pytest.mark.parametrize('resume', ['Website: https://engineer.example/projects', 'Website: engineer.example/projects'])
def test_url_explicitly_in_original_resume_is_supported(resume):
    assert agent(resume)._supported_web_address(Control('a','textbox','Portfolio URL'),'https://engineer.example/projects')


def test_exact_approved_url_is_supported_without_resume():
    value='https://github.com/approved-person'
    assert agent(known=value)._supported_web_address(Control('a','textbox','Github Profile*'),value)
    assert not agent(known=value)._supported_web_address(Control('a','textbox','Github Profile*'),value+'-invented')


def test_email_domain_is_not_personal_website_evidence():
    assert not agent('person@employer.example')._supported_web_address(Control('a','textbox','Website'),'https://employer.example')


def test_generic_homepage_is_rejected_before_any_browser_write():
    a=agent()
    a.tab=lambda page: page
    assert a.do(object(),Answer('a','Github Profile*','fill','https://github.com','resume'),
                Control('a','textbox','Github Profile*')) is False
