"""Mixed profile/account pages need ordinary application fields before authentication."""
from types import SimpleNamespace
import pytest
import form_fields
from page_agent import PageAgent


def setup_agent(monkeypatch, *, uploaded=False, answered=0, fields=()):
    agent=PageAgent.__new__(PageAgent)
    calls=[]
    agent.tab=lambda page: page
    agent.inventory_pass=lambda page: calls.append('inventory') or (['resume'] if uploaded else [])
    agent.snapshot=lambda page: ''
    agent.answer_what_is_known=lambda *args: (answered, [])
    monkeypatch.setattr(form_fields,'inventory',lambda page:list(fields))
    agent.account_blocker=''
    return agent,calls


def test_profile_upload_is_prepared_before_account_action(monkeypatch):
    a,calls=setup_agent(monkeypatch,uploaded=True)
    assert a._prepare_candidate_profile(object())=='changed'
    assert calls==['inventory']


def test_profile_known_answers_are_prepared_before_account_action(monkeypatch):
    a,_=setup_agent(monkeypatch,answered=2)
    assert a._prepare_candidate_profile(object())=='changed'


def test_missing_profile_answer_blocks_credentials_but_password_does_not(monkeypatch):
    fields=[form_fields.Field('pw','text',type='password',required=True),
            form_fields.Field('phone','text',label='Phone',required=True)]
    a,_=setup_agent(monkeypatch,fields=fields)
    assert a._prepare_candidate_profile(object())=='blocked'
    assert 'Phone' in a.account_blocker
    assert 'password' not in a.account_blocker.lower()


def test_only_account_credentials_remaining_is_ready(monkeypatch):
    fields=[form_fields.Field('pw','text',type='password',required=True),
            form_fields.Field('email','text',type='email',label='Email/Login',required=True)]
    a,_=setup_agent(monkeypatch,fields=fields)
    assert a._prepare_candidate_profile(object())=='ready'

def test_registration_step_prepares_before_credential_dispatch(monkeypatch, tmp_path):
    from tests.test_account_step import agent, serve, step, Assistant
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page()
        serve(page,'<h2>Create your profile</h2><label>Email<input type=email></label><label>Password<input type=password></label><label>Verify Password<input type=password></label><button>Submit Profile</button>')
        assistant=Assistant()
        assistant.adapter=lambda tab: SimpleNamespace(candidate_account_state=lambda tab:'registration')
        a=agent(assistant,tmp_path)
        a._prepare_candidate_profile=lambda page:'changed'
        assert step(page,a) is True
        assert assistant.calls==[]
        browser.close()
