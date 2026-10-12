"""Exercise the real flow's Review and ordinary handoff resume branches."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import apply_flow
import page_agent

@pytest.mark.parametrize('review', [True, False])
@pytest.mark.parametrize('decision', ['continue', 'reload_code', 'refresh', 'quit'])
def test_handoff_resumes_only_nonterminal_actions(monkeypatch, tmp_path, review, decision):
    page = SimpleNamespace(url='https://jobs.example.invalid/application', is_closed=lambda: False)
    state = {'parked': False, 'calls': 0, 'resumes': 0}
    def park(*args, **kwargs):
        state['parked'] = True
    def resume(*args):
        state['resumes'] += 1
        state['parked'] = False
    assistant = SimpleNamespace(save_progress=lambda *args: None, is_review_step=lambda p: review,
        human_handoff=park, raise_window=lambda *args: None, wait_for_signal=lambda *args, **kw: decision,
        resume_automation=resume)
    def run(p):
        state['calls'] += 1
        if state['calls'] == 1:
            return page_agent.Outcome('owner_needed', page, ['review step' if review else 'inspect this control'])
        assert not state['parked'], 'flow resumed the agent while automation remained parked'
        return page_agent.Outcome('submitted', page, ['the site confirmed the application'])
    fake = SimpleNamespace(run=run, tab=lambda p: p, uncertain_action_labels=lambda: [], notes=[], pages_read=1,
        note_owner_changes=lambda *args: None, _ensure_state=lambda: None,
        forget_sign_in_attempts=lambda **kw: None, remember_page_state=lambda *args: None)
    monkeypatch.setattr(page_agent, 'PageAgent', lambda *args, **kw: fake)
    monkeypatch.setattr(apply_flow, 'RuntimeEvent', None)
    for name in ['store_materials', 'remember_progress', 'write_recovery_checkpoint', 'delete_screenshots',
                 'refresh_answer_bank', 'save_stop_page']:
        monkeypatch.setattr(apply_flow, name, lambda *args, **kw: None)
    monkeypatch.setattr(apply_flow, 'hand_over', park)
    monkeypatch.setattr(apply_flow, 'load_latest_code', lambda agent, assistant, *args: assistant)
    tracker = Mock()
    job = SimpleNamespace(title='Network Engineer', company='Example', url=page.url)
    apply_flow.run_page_agent(assistant, page, None, SimpleNamespace(generate_cover_letters=False),
        None, None, job, tracker, 'job-1', tmp_path, tmp_path/'resume.pdf',
        SimpleNamespace(timeout=1), tmp_path/'signal.txt')
    assert state['resumes'] == (0 if decision == 'quit' else 1)
    assert state['calls'] == (1 if decision == 'quit' else 2)
