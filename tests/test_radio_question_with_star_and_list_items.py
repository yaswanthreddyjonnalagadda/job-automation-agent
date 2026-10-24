"""A radio question keeps its own words when the page marks it required with a star glyph and wraps each choice in a list
item (f228).

Lever draws "Are you legally authorized to work...?" as text, then a separate "✱" marker, then each Yes/No inside its
own list item. The reader took "✱" for the question of the first button and the first button's own label ("Yes") for the
question of the second, so neither matched "authorized to work" or "sponsorship": the agent left both questions blank and
every Lever application stopped there (Palantir, 23 October; Lever's Progress row read 0 of 2).
"""
import pytest

import page_agent

STAR = "✱"


def lever(marker):
    return f"""- generic [ref=e1]:
  - generic [ref=e2]:
    - generic [ref=e3]:
      - text: Are you legally authorized to work in the country for which you are applying?
      - generic [ref=e4]: {marker}
    - list [ref=e5]:
      - listitem [ref=e6]:
        - generic [ref=e7] [cursor=pointer]:
          - radio "Yes" [ref=e8]
          - generic [ref=e9]: "Yes"
      - listitem [ref=e10]:
        - generic [ref=e11] [cursor=pointer]:
          - radio "No" [ref=e12]
          - generic [ref=e13]: "No"
  - generic [ref=e14]:
    - generic [ref=e15]:
      - text: Will you now or in the future require sponsorship for employment visa status (e.g., H-1B, etc.)?
      - generic [ref=e16]: {marker}
    - list [ref=e17]:
      - listitem [ref=e18]:
        - generic [ref=e19] [cursor=pointer]:
          - radio "Yes" [ref=e20]
          - generic [ref=e21]: "Yes"
      - listitem [ref=e22]:
        - generic [ref=e23] [cursor=pointer]:
          - radio "No" [ref=e24]
          - generic [ref=e25]: "No"
"""


@pytest.mark.parametrize("marker", [STAR, "*", "＊", "∗", "(required)"])
def test_both_buttons_of_a_star_marked_question_belong_to_the_question(marker):
    radios = [c for c in page_agent.parse_snapshot(lever(marker)) if c.role == "radio"]
    assert [c.name for c in radios] == ["Yes", "No", "Yes", "No"]
    assert {c.question for c in radios[:2]} == {"Are you legally authorized to work in the country for which you are applying?"}
    assert {c.question for c in radios[2:]} == {"Will you now or in the future require sponsorship for employment visa status (e.g., H-1B, etc.)?"}


def test_a_star_alone_is_never_a_question():
    controls = page_agent.parse_snapshot(lever(STAR))
    assert not [c for c in controls if c.question.strip() in (STAR, "*")]
