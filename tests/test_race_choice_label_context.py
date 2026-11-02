"""A selected race's Hispanic qualifier is not the question being answered."""
import pytest
from dataclasses import replace

import concept_matcher
import page_agent
from test_workday_prompt_textbox import agent

OPTIONS=[
    'American Indian or Alaska Native (Not Hispanic or Latino) (United States of America)',
    'Asian (Not Hispanic or Latino) (United States of America)',
    'Black or African American (Not Hispanic or Latino) (United States of America)',
    'Hispanic or Latino (United States of America)',
]


@pytest.mark.parametrize('selected',OPTIONS[:2])
@pytest.mark.parametrize('offered',[[],OPTIONS])
def test_race_answer_comes_from_ethnicity_even_when_button_contains_qualifier(selected,offered):
    a=agent();a.profile=replace(a.profile,ethnicity='South Asian',hispanic_or_latino='No');a._profile_answer_library={}
    c=page_agent.Control('race','button','What is your race/ethnicity? '+selected,value=selected,options=offered)
    value,source=a.known_answer(c)
    assert c.question=='What is your race/ethnicity?'
    assert source=='profile.ethnicity'
    assert value==(OPTIONS[1] if offered else 'South Asian')


def test_race_options_with_hispanic_qualifiers_do_not_become_hispanic_only_question():
    assert concept_matcher.confirm_concept('ETHNICITY_RACE',OPTIONS)=='ETHNICITY_RACE'


def test_actual_hispanic_only_options_still_disambiguate_ethnic_origin():
    assert concept_matcher.confirm_concept('ETHNICITY_RACE',[
        'Hispanic or Latino', 'Not Hispanic or Latino',
    ])=='HISPANIC_OR_LATINO'
