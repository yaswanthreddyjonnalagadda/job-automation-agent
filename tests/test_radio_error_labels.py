"""Regression test for f214: radios with error accessible names adopting child/sibling labels."""
from page_agent import parse_snapshot

def test_radio_with_validation_error_name_adopts_child_label():
    snapshot = """
- generic:
  - radiogroup "Are you on a lay-off and subject to recall?":
    - radio "Error Add response." [ref=e1]:
      - generic: "Yes"
    - radio "Error Add response." [ref=e2]:
      - generic: "No"
"""
    controls = parse_snapshot(snapshot)
    radios = [c for c in controls if c.role == "radio"]
    assert len(radios) == 2
    assert radios[0].name == "Yes"
    assert radios[1].name == "No"
    assert radios[0].ref == "e1"
    assert radios[1].ref == "e2"
    assert radios[0].group == "Are you on a lay-off and subject to recall?"
    assert radios[1].group == "Are you on a lay-off and subject to recall?"

