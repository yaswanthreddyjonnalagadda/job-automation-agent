"""Owner review must reconcile durable and in-memory creation holds together."""
from types import SimpleNamespace

import pytest

import account_state
from page_agent import PageAgent


HOST = 'jobs-tenant.icims.com'


class Tracker:
    def __init__(self, pending):
        self.cp = {'application_key': 'application', 'pending_action': pending}

    def read_checkpoint(self, _key):
        return self.cp

    def write_checkpoint(self, _key, payload):
        self.cp = payload


@pytest.mark.parametrize('owner_acted', [False, True])
def test_resume_reconciles_memory_only_after_owner_clears_pending_creation(owner_acted):
    tracker = Tracker('account_creation_dispatched@' + HOST)
    agent = PageAgent(SimpleNamespace(submission_key='application'), None, None, None, '',
                      SimpleNamespace(url='https://' + HOST, company='Example'), tracker, 'application')
    agent._current_host = HOST
    agent._created_at = {HOST, 'other.example.com'}
    agent.forget_sign_in_attempts(owner_acted=owner_acted)
    step = account_state.next_step(account_state.AccountState(account_state.CREATE_FORM),
                                   account_state.Memory(created=HOST in agent._created_at))
    assert step.action == (account_state.CREATE if owner_acted else account_state.FOR_OWNER)
    assert 'other.example.com' in agent._created_at
    assert bool(tracker.cp['pending_action']) is not owner_acted


def test_resume_keeps_confirmed_creation_history_for_verification_recovery():
    agent = PageAgent(SimpleNamespace(submission_key='application'), None, None, None, '',
                      SimpleNamespace(url='https://' + HOST, company='Example'), Tracker(''), 'application')
    agent._current_host = HOST
    agent._created_at = {HOST}
    agent.forget_sign_in_attempts(owner_acted=True)
    assert HOST in agent._created_at


def test_failed_checkpoint_clear_keeps_memory_hold():
    class Unwritable(Tracker):
        def write_checkpoint(self, _key, payload):
            raise OSError('synthetic unavailable storage')
    tracker = Unwritable('account_creation_dispatched@' + HOST)
    agent = PageAgent(SimpleNamespace(submission_key='application'), None, None, None, '',
                      SimpleNamespace(url='https://' + HOST, company='Example'), tracker, 'application')
    agent._current_host = HOST
    agent._created_at = {HOST}
    agent.forget_sign_in_attempts(owner_acted=True)
    assert HOST in agent._created_at
    assert tracker.cp['pending_action']
