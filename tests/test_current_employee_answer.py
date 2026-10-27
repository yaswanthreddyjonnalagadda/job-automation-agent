"""Current employment is resolved from the current employer, not former jobs."""
from types import SimpleNamespace

import pytest
import answer_engine


@pytest.mark.parametrize('current,expected', [('Acme','Yes'),('Globex','No'),('','')])
@pytest.mark.parametrize('question', [
    'Are you a current employee of Acme or its affiliates?',
    'Are you a current Acme employee?',
])
def test_current_employee_question_uses_current_employer_only(current,expected,question):
    ask=answer_engine.Ask(question=question,options=['Yes','No'],role='radiogroup',
        profile=SimpleNamespace(current_employer=current),company='Acme',employers=['Acme','Globex'])
    assert answer_engine.answer(ask)[0] == expected


def test_a_relative_employment_question_is_not_current_self_employment():
    ask=answer_engine.Ask(question='Is your relative a current employee of Acme?',options=['Yes','No'],
        role='radiogroup',profile=SimpleNamespace(current_employer='Acme'),company='Acme')
    assert answer_engine.answer(ask)[0] == ''
