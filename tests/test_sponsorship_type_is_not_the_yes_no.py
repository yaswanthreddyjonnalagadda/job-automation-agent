"""'Please specify the type' after a sponsorship question is not the yes/no sponsorship question (f285).

Color Health (CareerPuck), 1 November 2026: 'If you will require the company's assistance or sponsorship for work
authorization, please specify the type (e.g., H-1B transfer, TN, F-1 to H-1B, etc.)' is a free-text box. The bare word
'sponsor' put it in the VISA_SPONSORSHIP concept, which answers Yes or No, so the box was filled with 'Yes' and the
correct answer could not replace it.
"""
import pytest

import concept_matcher

TYPE_QUESTIONS = [
    "If you will require the company’s assistance or sponsorship for work authorization, please specify the type "
    "(e.g., H-1B transfer, TN, F-1 to H-1B, etc.).",
    "What type of visa sponsorship would you need?",
    "Which visa would require sponsorship?",
]
YES_NO_QUESTIONS = [
    "Will you require the company’s assistance or sponsorship with work authorization now or in the future?",
    "Do you now or in the future require visa sponsorship?",
    "Will you require sponsorship?",
]


@pytest.mark.parametrize("question", TYPE_QUESTIONS)
def test_a_question_asking_for_the_type_is_not_the_yes_no_concept(question):
    assert concept_matcher.match_concept(question) != "VISA_SPONSORSHIP"


@pytest.mark.parametrize("question", YES_NO_QUESTIONS)
def test_the_yes_no_sponsorship_question_still_is(question):
    assert concept_matcher.match_concept(question) == "VISA_SPONSORSHIP"
