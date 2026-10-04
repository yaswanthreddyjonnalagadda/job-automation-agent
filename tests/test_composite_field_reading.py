import page_agent

def test_phone_wrapper_and_dial_code_are_not_extra_questions():
    snapshot = '''- textbox [ref=e1]:
  - generic:
    - text: Primary Phone Number
    - generic: "*"
  - button [ref=e2]:
    - paragraph: "+1"
  - textbox [ref=e3]:
    - /placeholder: xxx xxx xxxx
    - text: (202) 555-0100
'''
    controls = page_agent.parse_snapshot(snapshot)
    boxes = [c for c in controls if c.role == 'textbox']
    assert len(boxes) == 1
    assert boxes[0].ref == 'e3'
    assert boxes[0].question == 'Primary Phone Number'
    assert boxes[0].answer == '(202) 555-0100'

def test_link_fragments_do_not_replace_radio_question():
    snapshot = '''- paragraph [ref=e1]:
  - generic:
    - text: Do you consent to receiving text communications related to your job application via SMS?
    - link "Terms of Use" [ref=e2]
    - text: and
    - link "Privacy Policy" [ref=e3]
    - text: for more details.
- radiogroup [ref=e4]:
  - radio [ref=e5]
  - paragraph: Yes
  - radio [ref=e6]
  - paragraph: No
'''
    controls = page_agent.parse_snapshot(snapshot)
    radios = [c for c in controls if c.role == 'radio']
    assert len(radios) == 2
    assert all(c.question.startswith('Do you consent to receiving text communications') for c in radios)
    assert [c.name for c in radios] == ['Yes', 'No']
