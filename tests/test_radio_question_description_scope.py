"""A field's marked question survives descriptive radio-group names."""
import pytest

from page_agent import parse_snapshot


@pytest.mark.parametrize('group_name', ['', ' "Explanation of the answer categories."'])
def test_marked_field_question_owns_described_radios(group_name):
    snapshot=f'''- generic [ref=e1]:
  - generic [ref=e2]:
    - generic [ref=e3]: Is this your approved preference?*
    - generic [ref=e4]:
      - generic [ref=e5]: Explanation of the answer categories.
      - radiogroup{group_name} [ref=e6]:
        - radio "Yes" [ref=e7]
        - radio "No" [ref=e8]
  - generic [ref=e9]:
    - generic [ref=e10]: A different question*
    - radiogroup [ref=e11]:
      - radio "Yes" [ref=e12]
      - radio "No" [ref=e13]
'''
    controls=parse_snapshot(snapshot)
    by_ref={c.ref:c for c in controls}
    assert by_ref['e7'].question == 'Is this your approved preference?'
    assert by_ref['e8'].question == 'Is this your approved preference?'
    assert 'different question' in by_ref['e12'].question
    assert by_ref['e12'].question == by_ref['e13'].question


def test_an_explicit_question_on_a_radio_group_is_preserved():
    snapshot='''- generic [ref=e1]:
  - generic [ref=e2]: Section label*
  - generic [ref=e3]: Additional context.
  - radiogroup "Is this the actual question?" [ref=e4]:
    - radio "Yes" [ref=e5]
    - radio "No" [ref=e6]
'''
    assert next(c for c in parse_snapshot(snapshot) if c.ref=='e5').question == 'Is this the actual question?'
