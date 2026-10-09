import pytest
from types import SimpleNamespace
import option_match
import concept_matcher
import page_agent


EDUCATION = ['Select One', 'High School or equivalent', 'Vocational Studies',
             'Some College/University', 'College/University Graduate',
             'Some Post-Graduate Studies', 'Post-Graduate Degree']


@pytest.mark.parametrize('degree,expected', [("Master's Degree", 6), ('Master of Science', 6),
                                          ('Doctorate', 6), ("Bachelor's Degree", 4)])
def test_completed_degree_matches_completed_category(degree, expected):
    assert option_match.best_option(EDUCATION, degree) == expected
    assert concept_matcher.best_option_match(degree, EDUCATION) == EDUCATION[expected]


def test_general_postgraduate_category_never_invents_specific_degree():
    assert option_match.best_option(["Master's Degree", 'Doctorate'], 'Post-Graduate Degree') is None
    assert option_match.best_option(['Some Post-Graduate Studies'], "Master's Degree") is None
    assert option_match.best_option(["Master's Degree", 'Post-Graduate Degree'], "Master's Degree") == 0


def test_notice_period_uses_no_notice_category_only_in_notice_question():
    agent = page_agent.PageAgent.__new__(page_agent.PageAgent)
    agent._chosen_instead = {}
    agent._alternatives_for = lambda value: []
    attempted = []
    agent._choose_exact = lambda page, control, value, controls: attempted.append(value) or value == 'Not Applicable'
    notice = page_agent.Control('e1', 'button', name='Select One', container='Notice Period*')
    assert agent.choose(None, notice, 'Immediately')
    assert attempted == ['Immediately', 'Not Applicable']
    attempted.clear()
    other = page_agent.Control('e2', 'button', name='Select One', container='Preferred start date')
    assert not agent.choose(None, other, 'Immediately')
    assert attempted == ['Immediately']


@pytest.mark.parametrize('preference,expected', [('Email', 'Continue with email'),
                                               ('Phone', 'Continue with phone number')])
def test_identity_channel_selects_preference_instead_of_contact_fact(preference, expected):
    agent = page_agent.PageAgent.__new__(page_agent.PageAgent)
    agent.profile = SimpleNamespace(preferred_contact_method=preference, phone_mobile='202-555-0100')
    control = page_agent.Control('e1', 'radiogroup', name='How would you like to verify your identity?',
                                options=['Continue with phone number', 'Continue with email'])
    assert agent.known_answer(control) == (expected, 'profile.preferred_contact_method')


def test_real_selects_commit_profile_categories(tmp_path):
    from playwright.sync_api import sync_playwright
    from test_page_agent import make_agent
    import config
    resume = tmp_path / 'resume.pdf'
    resume.write_bytes(b'%PDF-1.4 fixture')
    profile = config.UserProfile(availability_to_start='Immediately',
                                education=(("Master's Degree", 'Computer Science', 'Example University', '2022'),))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('''<label>Notice Period*<select id="notice"><option>Select One</option>
          <option>Not Applicable</option><option>2 Weeks</option></select></label>
          <label>Highest level of education completed*<select id="education">
          <option>Select One</option><option>Some Post-Graduate Studies</option>
          <option>Post-Graduate Degree</option></select></label>''')
        agent = make_agent(SimpleNamespace(), resume, profile=profile)
        controls = page_agent.parse_snapshot(agent.snapshot(page))
        for control in [c for c in controls if c.role == 'combobox']:
            value, source = agent.known_answer(control)
            assert agent.do(page, page_agent.Answer(control.ref, control.question, 'choose', value, source), control)
        assert page.locator('#notice').input_value() == 'Not Applicable'
        assert page.locator('#education').input_value() == 'Post-Graduate Degree'
        browser.close()
