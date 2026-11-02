"""A required question is required however long its label is (f295).

Fastly (Greenhouse), 1 November 2026: "Are you subject to any agreements that may restrict your ability to be employed
in this position ...?*" (139 characters) and "How much relevant professional experience do you have building distributed
systems ...?*" (159) are marked required, but the reader of the page's stars only took a label of up to 120
characters. Both were read as optional, so the agent left them blank "without asking a model", and the last check --
which looks at the page itself, with no length limit -- then stopped the run for two required fields it had never asked
anybody about. Two readers of one fact (is this question required?) had two different limits.
"""
import pytest

import page_agent
from page_agent import required_questions, split_optional

Q_AGREEMENTS = ("Are you subject to any agreements that may restrict your ability to be employed in this position or "
                "perform the functions of this position?")
Q_EXPERIENCE = ("How much relevant professional experience do you have building distributed systems, container "
                "orchestration platforms, automating public cloud workloads, etc.?")

# The shape Greenhouse's job board draws, as the agent reads it: the label is a text line that ends with the star,
# and the dropdown under it is named by the question alone, without the star.
FASTLY_FRAGMENT = f"""
            - generic [ref=f14e334]:
              - generic [ref=f14e336]:
                - generic [ref=f14e337]: {Q_AGREEMENTS}*
                - generic [ref=f14e338]:
                  - generic [ref=f14e343]:
                    - generic [ref=f14e344]: Select...
                    - combobox "{Q_AGREEMENTS}" [ref=f14e346]
                  - button "Toggle flyout" [ref=f14e348] [cursor=pointer]
            - generic [ref=f14e356]:
              - generic [ref=f14e357]: {Q_EXPERIENCE}*
              - generic [ref=f14e362]:
                - generic [ref=f14e364]: Select...
                - combobox "{Q_EXPERIENCE}" [ref=f14e366]
              - button "Toggle flyout" [ref=f14e368] [cursor=pointer]
"""


def test_the_two_long_fastly_questions_are_required():
    required = required_questions(FASTLY_FRAGMENT)
    assert Q_AGREEMENTS in required
    assert Q_EXPERIENCE in required


def question_of_length(n: int) -> str:
    base = "Do you have professional experience building and running large distributed systems in production"
    text = (base + " " + "and more " * 80)[: n - 1].rstrip()
    return text + "?"


STAR_SHAPES = {
    "text line ending in a star": lambda q: f"- generic [ref=e1]: {q}*\n- combobox \"{q}\" [ref=e2]\n",
    "text line, a space, then a star": lambda q: f"- generic [ref=e1]: {q} *\n- combobox \"{q}\" [ref=e2]\n",
    "label text, the star on the next line": lambda q: f"- text: {q}\n  - generic [ref=e3]: *\n- combobox \"{q}\" [ref=e2]\n",
    "the control's own name ends in a star": lambda q: f'- combobox "{q}*" [ref=e2]\n',
    "the star is a node of its own": lambda q: f'- generic "{q}" [ref=e1]: "*"\n- combobox "{q}" [ref=e2]\n',
}


@pytest.mark.parametrize("shape", STAR_SHAPES)
@pytest.mark.parametrize("length", [20, 80, 119, 120, 121, 139, 159, 200, 201, 300, 450, 590])
def test_a_marked_question_is_required_at_any_length(shape, length):
    question = question_of_length(length)
    assert len(question) <= length
    assert question in required_questions(STAR_SHAPES[shape](question)), (
        f"a {len(question)}-character label with its star ({shape}) was not read as required")


def test_a_required_question_is_not_left_blank_as_optional():
    """What the agent does with the question: only an optional one is left without asking anyone."""
    required = required_questions(FASTLY_FRAGMENT)
    still_open = [q + ("*" if any(page_agent._same_question(q, r) for r in required) else "")
                  for q in (Q_AGREEMENTS, Q_EXPERIENCE, "Do you have a middle name?")]
    to_answer, left_blank = split_optional(still_open, required)
    assert Q_AGREEMENTS + "*" in to_answer and Q_EXPERIENCE + "*" in to_answer
    assert left_blank == ["Do you have a middle name?"]
