"""A known text answer belongs to its exact question, not a later substring match."""
import re
from types import SimpleNamespace
import pytest
from browser_automation import JobApplicationAssistant

@pytest.mark.parametrize('questions', [('Address', 'Address 2'), ('Address 2', 'Address'), ('Employer', 'Previous Employer')])
def test_resolved_text_answer_does_not_leak_to_another_question(questions):
    assistant=JobApplicationAssistant.__new__(JobApplicationAssistant)
    known='Address' if 'Address' in questions else 'Employer'
    assistant._standard_answer_rules=lambda profile: [(re.compile('^'+known+'$'), ['Synthetic value'])]
    assistant.adapter=lambda page: SimpleNamespace(set_date=lambda *args: False)
    written={}
    assistant.set_value=lambda page, selector, value, question, **kw: written.setdefault(question,value)
    page=SimpleNamespace(evaluate=lambda script:[{'id':str(i),'q':q,'placeholder':''} for i,q in enumerate(questions)])
    assistant._answer_text_questions(page, SimpleNamespace())
    assert written=={known:'Synthetic value'}
