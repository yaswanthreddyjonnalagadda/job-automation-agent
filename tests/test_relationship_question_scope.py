"""Domestic/military partner status is not a relative's employment relationship."""
from types import SimpleNamespace

import pytest

import answer_engine


@pytest.mark.parametrize('question, expected', [
    ('Are you a current or former military spouse or domestic partner?', ''),
    ('Do you have a spouse or domestic partner?', ''),
    ('Is your spouse or domestic partner employed by Example Corp?', 'No'),
    ('Have you ever attended a community college or taken classes there?', ''),
    ('Were you referred to Example Corp by an employee or customer?', ''),
])
def test_unrelated_negative_does_not_establish_personal_history(question,expected):
    profile=SimpleNamespace(relatives_employed_here='No')
    value,_=answer_engine.answer(answer_engine.Ask(question=question,options=['Yes','No'],profile=profile,company='Example Corp'))
    assert value==expected
