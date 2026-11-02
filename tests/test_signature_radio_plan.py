from dataclasses import replace
import page_agent
from test_workday_prompt_textbox import agent,browser,page  # noqa: F401


def test_signature_plan_accepts_once_and_fills_its_scoped_legal_name():
    s='''- generic "eSignature" [ref=e1]:
  - generic "Application E-Signature is required" [ref=e2]:
    - radiogroup "Application E-Signature" [ref=e3]:
      - radio "I Accept" [ref=e4]
      - radio "I Decline" [ref=e5]
    - textbox "Type in your name" [ref=e6]
- textbox "Type in your name" [ref=e7]
'''
    a=agent();a.profile=replace(a.profile,sign_attestations=True)
    cs=page_agent.parse_snapshot(s)
    plan=a.profile_plan(cs,set(),[],s)
    actions={x.ref:(x.action,x.value) for x in plan.answers}
    assert actions.get('e4')==('check','checked')
    assert 'e5' not in actions
    assert actions.get('e6')==('fill','Example Candidate')
    assert 'e7' not in actions


def test_signature_plan_still_requires_standing_authorization():
    a=agent();a.profile=replace(a.profile,sign_attestations=False)
    cs=page_agent.parse_snapshot('- radiogroup "Application E-Signature" [ref=e1]:\n  - radio "I Accept" [ref=e2]\n  - radio "I Decline" [ref=e3]')
    assert not a.profile_plan(cs,set(),[]).answers
