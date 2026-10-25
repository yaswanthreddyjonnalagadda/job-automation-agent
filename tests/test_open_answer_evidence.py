"""Cross-portal regression for Fal's profile-only technical essays (f209)."""
import json
from types import SimpleNamespace

import pytest

import open_answers
from claude_integration import ClaudeClient
from page_agent import PageAgent, Answer, Control

QUESTION = 'Describe your networking experience, including the size and scale of environments supported.'
RESUME = 'I supported BGP routing across 40 sites and two cloud regions.'
GOOD = 'I supported BGP routing across 40 sites and two cloud regions.'
BIO = 'I have six years of experience. I am a network engineer. I hold a degree in computing.'


def scripted(*responses):
    calls = []
    def ask(system, user, tokens):
        calls.append((system, user))
        response = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return json.dumps(response)
    return ask, calls


def test_original_resume_is_used_without_nonexistent_browser_extractor():
    agent = PageAgent.__new__(PageAgent)
    agent.resume = SimpleNamespace(raw_text=RESUME)
    agent.assistant = SimpleNamespace()
    agent.job = SimpleNamespace()
    assert agent._answer_resume_text() == RESUME


def test_original_resume_supersedes_empty_legacy_cache():
    agent = PageAgent.__new__(PageAgent)
    agent.resume = SimpleNamespace(raw_text=RESUME)
    agent._resume_text_cache = ''
    assert agent._answer_resume_text() == RESUME


@pytest.mark.parametrize('draft,problem', [
    (BIO, 'missing requested size and scale'),
    ('I do not have specific Linux kernel tuning projects to share.', 'unsupported claim of no kernel tuning experience'),
    ('I supported 500 sites across the enterprise.', 'invented 500-site metric from the job posting'),
])
def test_rejected_content_never_returns_a_fillable_answer(draft, problem):
    ask, calls = scripted({'answer': draft}, {'supported': False, 'complete': False, 'issues': [problem]})
    answer, issues = open_answers.write(QUESTION, ask=ask, facts='Six years of experience', resume_text=RESUME)
    assert answer == ''
    assert issues
    assert len(calls) <= 4


def test_content_feedback_can_repair_an_answer():
    ask, calls = scripted({'answer': BIO}, {'supported': True, 'complete': False, 'issues': ['missing scale']},
                          {'answer': GOOD}, {'supported': True, 'complete': True, 'issues': []})
    answer, issues = open_answers.write(QUESTION, ask=ask, facts='', resume_text=RESUME,
                                        limits=open_answers.Limits(max_words=20))
    assert answer == GOOD and issues == []
    assert 'missing scale' in calls[2][1]


@pytest.mark.parametrize('response', [{}, {'supported': 'true', 'complete': True, 'issues': []},
    {'supported': True, 'complete': True}, {'supported': True, 'complete': True, 'issues': ['missing scale']},
    RuntimeError('provider unavailable')])
def test_review_fails_closed_on_invalid_or_unavailable_verdict(response):
    ask, _ = scripted(response)
    assert open_answers.review(QUESTION, GOOD, ask=ask, facts='', resume_text=RESUME)


def test_review_checks_the_exact_fitted_text():
    ask, calls = scripted({'answer': GOOD + ' I improved routing reliability.'},
                          {'supported': True, 'complete': True, 'issues': []})
    answer, issues = open_answers.write(QUESTION, ask=ask, facts='', resume_text=RESUME,
                                       limits=open_answers.Limits(max_chars=len(GOOD)))
    assert answer == GOOD and not issues
    assert json.loads(calls[1][1])['answer'] == answer


def test_review_prompt_distinguishes_missing_facts_from_negative_experience():
    ask, calls = scripted({'supported': True, 'complete': True, 'issues': []})
    assert not open_answers.review(QUESTION, GOOD, ask=ask, facts='', resume_text=RESUME)
    assert 'absence' in calls[0][0].lower()
    assert 'scale' in calls[0][0].lower()


def test_related_experience_is_requested_and_accepted_without_exact_technology():
    answer = 'My related work includes BGP routing across 40 sites and two cloud regions.'
    ask, calls = scripted({'answer': answer},
                          {'supported': True, 'complete': True, 'issues': []})
    result, issues = open_answers.write('Describe your Linux networking experience.',
                                       ask=ask, facts='', resume_text=RESUME,
                                       limits=open_answers.Limits(max_words=40))
    assert result == answer and not issues
    assert 'closest' in calls[0][0] and 'transferable skills' in calls[0][0]
    assert 'Do not mark incomplete' in calls[1][0]
    assert 'without claiming undocumented' in calls[0][0]
    assert 'takes precedence' in calls[1][0]
    assert 'not an undocumented direct project' in calls[1][0]
    assert 'personal claim and still requires evidence' in calls[1][0]


def test_late_resume_evidence_is_available():
    ask, calls = scripted({'answer': GOOD}, {'supported': True, 'complete': True, 'issues': []})
    open_answers.write(QUESTION, ask=ask, facts='', resume_text=('General history.\n' * 450) + RESUME,
                       limits=open_answers.Limits(max_words=20))
    assert RESUME in calls[0][1]


def test_planner_cannot_bypass_content_gate_by_claiming_profile_source():
    agent = PageAgent.__new__(PageAgent)
    agent.claude = SimpleNamespace(review_open_answer=lambda *a, **kw: ['missing scale'])
    agent.resume = SimpleNamespace(raw_text=RESUME)
    agent.profile = None
    agent.job = SimpleNamespace(raw_text='')
    agent.known_answer = lambda ctrl: ('', '')
    ctrl = Control(ref='a', role='textbox', name=QUESTION)
    answer = Answer('a', QUESTION, 'fill', BIO, 'profile.experience')
    assert agent._open_answer_refusal(answer, ctrl, {'multiline': True})


def test_approved_answer_bypasses_ai_review():
    agent = PageAgent.__new__(PageAgent)
    agent.known_answer = lambda ctrl: (GOOD, 'owner_earlier_answer')
    ctrl = Control(ref='a', role='textbox', name=QUESTION)
    assert not agent._open_answer_refusal(Answer('a', QUESTION, 'fill', GOOD), ctrl, {'multiline': True})


def test_content_review_routes_to_answer_provider_not_document_writer():
    from ai_choice import Brain
    answers = SimpleNamespace(review_open_answer=lambda: 'answer provider')
    brain = Brain(answers, SimpleNamespace())
    assert brain.review_open_answer() == 'answer provider'


def test_gemini_content_review_routes_to_answer_provider_not_document_writer():
    from gemini_integration import GeminiBrain
    brain = GeminiBrain.__new__(GeminiBrain)
    brain._answers = SimpleNamespace(review_open_answer=lambda: 'gemini review')
    brain._documents = SimpleNamespace(review_open_answer=lambda: 'wrong writer')
    assert brain.review_open_answer() == 'gemini review'


def test_review_cache_is_specific_to_answer_and_evidence():
    client = ClaudeClient.__new__(ClaudeClient)
    ask, calls = scripted({'supported': True, 'complete': True, 'issues': []})
    client._call = lambda system, user_message, max_tokens: ask(system, user_message, max_tokens)
    for _ in range(2):
        assert not client.review_open_answer(QUESTION, GOOD, resume_text=RESUME)
    assert len(calls) == 1
    client.review_open_answer(QUESTION, GOOD + ' More.', resume_text=RESUME)
    client.review_open_answer(QUESTION, GOOD, resume_text=RESUME + ' New evidence.')
    assert len(calls) == 3


def test_short_complete_answer_is_not_padded_to_default_word_target():
    ask, calls = scripted({'answer': GOOD}, {'supported': True, 'complete': True, 'issues': []})
    answer, issues = open_answers.write(QUESTION, ask=ask, facts='', resume_text=RESUME)
    assert answer == GOOD and not issues
    assert len(calls) == 2


def test_requested_minimum_still_applies():
    assert open_answers.problems(GOOD, open_answers.Limits(min_words=30))


def test_retrieval_preserves_relevant_evidence_beyond_long_resume_prefix():
    resume = ('General background text.\n' * 1000) + RESUME
    assert RESUME in open_answers.evidence_text(QUESTION, resume)
    assert len(open_answers.evidence_text(QUESTION, resume)) <= 12000


def test_page_plan_rejection_leaves_browser_field_blank():
    from playwright.sync_api import sync_playwright
    import config
    import safety
    from browser_automation import JobApplicationAssistant
    from page_agent import PagePlan
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    agent = PageAgent(assistant, SimpleNamespace(review_open_answer=lambda *a, **kw: ['missing scale']),
                      SimpleNamespace(auto_submit=False, ats_email=''), config.UserProfile(),
                      SimpleNamespace(raw_text=RESUME), SimpleNamespace(raw_text=''), resume_file=None)
    agent.known_answer = lambda ctrl: ('', '')
    agent.refusal = lambda *a: ''
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(f'<label for="answer">{QUESTION}*</label><textarea id="answer" required></textarea>')
        agent.locate = lambda page, ref: page.locator('#answer')
        ctrl = Control(ref='a', role='textbox', name=QUESTION + '*')
        plan = PagePlan(answers=[Answer('a', QUESTION, 'fill', BIO, 'profile.experience')])
        assert agent.apply_answers(page, plan, [ctrl]) == []
        assert page.locator('#answer').input_value() == ''
        assert plan.for_owner[0]['required'] is True
        assert 'missing scale' in plan.for_owner[0]['reason']
        browser.close()


def test_planner_cannot_append_claims_to_an_approved_answer():
    agent = PageAgent.__new__(PageAgent)
    agent.known_answer = lambda ctrl: (GOOD, 'owner_earlier_answer')
    agent.claude = SimpleNamespace(review_open_answer=lambda *a, **kw: ['unsupported appended claim'])
    agent.resume = SimpleNamespace(raw_text=RESUME)
    agent.profile = None
    agent.job = SimpleNamespace(raw_text='')
    ctrl = Control(ref='a', role='textbox', name=QUESTION)
    assert agent._open_answer_refusal(Answer('a', QUESTION, 'fill', GOOD + ' I led 1000 engineers.'),
                                      ctrl, {'multiline': True})


def test_content_approval_does_not_override_required_minimum():
    ask, _ = scripted({'answer': GOOD}, {'supported': True, 'complete': True, 'issues': []}, {'answer': GOOD})
    answer, issues = open_answers.write(QUESTION, ask=ask, facts='', resume_text=RESUME,
                                        limits=open_answers.Limits(min_words=30))
    assert answer == '' and any('required minimum' in issue for issue in issues)


def test_runtime_passes_original_resume_to_writer_and_fills_reviewed_answer():
    from playwright.sync_api import sync_playwright
    import config
    import safety
    from browser_automation import JobApplicationAssistant
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    client = ClaudeClient.__new__(ClaudeClient)
    contexts = []
    def call(system, user_message, max_tokens):
        contexts.append(user_message)
        assert RESUME in user_message
        if system == open_answers._REVIEW_SYSTEM:
            return json.dumps({'supported': True, 'complete': True, 'issues': []})
        return json.dumps({'answer': GOOD})
    client._call = call
    client.plan_page = lambda *a: {'page_kind': 'application_form', 'answers': [], 'leave_for_owner': [],
                                  'next': {'kind': 'final_submit', 'label': 'Submit Application'}}
    job = SimpleNamespace(title='Network Engineer', company='Example', url='https://jobs.example.com/apply', raw_text='')
    agent = PageAgent(assistant, client, SimpleNamespace(auto_submit=False, ats_email=''),
                      config.UserProfile(), SimpleNamespace(raw_text=RESUME), job, resume_file=None)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.route('https://jobs.example.com/**', lambda route: route.fulfill(
            content_type='text/html', body=f'<h1>Application</h1><form><label for="answer">{QUESTION}*</label>'
            '<textarea id="answer" required></textarea><button type="submit">Submit Application</button></form>'))
        page.goto(job.url)
        outcome = agent.run(page)
        assert outcome.kind == 'owner_needed'
        assert page.locator('#answer').input_value() == GOOD
        assert contexts
        browser.close()


def test_transient_review_failure_is_not_cached_for_future_attempts():
    client = ClaudeClient.__new__(ClaudeClient)
    ask, calls = scripted(RuntimeError('temporary'), {'supported': True, 'complete': True, 'issues': []})
    client._call = lambda system, user_message, max_tokens: ask(system, user_message, max_tokens)
    assert client.review_open_answer(QUESTION, GOOD, resume_text=RESUME)
    assert not client.review_open_answer(QUESTION, GOOD, resume_text=RESUME)
    assert len(calls) == 2


def test_long_single_line_resume_keeps_relevant_tail_evidence():
    resume = ('General background. ' * 900) + RESUME
    assert '40 sites' in open_answers.evidence_text('Describe BGP routing experience.', resume)


def test_planner_reviews_fitted_text_before_browser_can_truncate_it():
    agent = PageAgent.__new__(PageAgent)
    seen = []
    def review(question, value, **kwargs):
        seen.append(value)
        return []
    agent.known_answer = lambda ctrl: ('', '')
    agent.claude = SimpleNamespace(review_open_answer=review)
    agent.resume = SimpleNamespace(raw_text=RESUME)
    agent.profile = None
    agent.job = SimpleNamespace(raw_text='')
    ctrl = Control(ref='a', role='textbox', name=QUESTION)
    answer = Answer('a', QUESTION, 'fill', GOOD + ' I improved routing reliability.')
    assert not agent._open_answer_refusal(answer, ctrl, {'multiline': True, 'maxlength': len(GOOD)})
    assert answer.value == GOOD
    assert seen == [GOOD]
