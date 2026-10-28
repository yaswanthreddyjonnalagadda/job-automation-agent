"""A question the site flags with its own "Required" alert is a required question, whatever its name says (f273).

Walmart, 27 October 2026, Military Service: "Have you already left Active Duty ..." has no asterisk in its (410-character)
name. The site shows a red "Required" alert inside it once Continue is pressed. The run read the question as optional
and left it blank, took the bare word "Required" for a label and not an error, and pressed Continue three times until the
loop guard stopped it for the owner.
"""
import pytest

from page_agent import page_errors, required_questions

LONG_NAME = ('Have you already left Active Duty, or will you leave Active Duty in the near future by separation with a '
             'favorable characterization of discharge OR return to the reserves, other than “Yes”.')


def walmart_page(alert_under="Q2", asterisk_on_q1=True):
    q1 = "Do you have Active Duty or Guard/Reserve experience?" + ("*" if asterisk_on_q1 else "")
    alert = ("                    - alert [ref=e50]:\n"
             "                        - text: Required\n")
    return (
        '                  - radiogroup "' + q1 + '" [ref=e10]:\n'
        '                        - radio "No" [checked] [ref=e11]\n'
        "                  - 'radiogroup \"" + LONG_NAME + "\" [ref=e20]':\n"
        '                      - generic [ref=e21]: ' + LONG_NAME[:60] + '\n'
        '                        - radio "Yes" [ref=e22]\n'
        '                        - radio "Not Applicable" [ref=e23]\n'
        + (alert if alert_under == "Q2" else "") +
        '                  - radiogroup "Are you the Spouse/Partner of someone in the Uniformed Services?*" [ref=e30]:\n'
        '                        - radio "No" [checked] [ref=e31]\n'
        '                  - button "Continue" [ref=e60]\n')


def test_a_question_with_an_inline_required_alert_is_a_required_question():
    required = required_questions(walmart_page())
    assert LONG_NAME in required
    assert "Do you have Active Duty or Guard/Reserve experience?" in required      # the starred ones still count


def test_without_the_alert_that_question_stays_optional():
    assert LONG_NAME not in required_questions(walmart_page(alert_under=None))


def test_the_alert_names_the_question_in_what_the_form_says():
    errors = page_errors(walmart_page())
    assert errors and "Required" in errors[0] and LONG_NAME[:40] in errors[0]


def test_a_label_that_merely_contains_the_word_required_is_still_not_an_error():
    snapshot = '- generic [ref=e1]: "* Indicates a required field"\n- button "Degree Graduate Required" [ref=e2]'
    assert page_errors(snapshot) == [] and required_questions(snapshot) == set()


@pytest.mark.parametrize("text", ["Required", "This field is required", "Field is required."])
def test_the_alert_wordings_that_mean_the_same_thing(text):
    snapshot = ('- group "Preferred contact method" [ref=e1]:\n'
                '    - radio "Email" [ref=e2]\n'
                '    - alert [ref=e3]:\n'
                f'        - text: {text}\n')
    assert "Preferred contact method" in required_questions(snapshot)


def test_an_alert_that_is_not_inside_a_question_marks_nothing():
    snapshot = '- main [ref=e1]:\n- alert [ref=e2]:\n    - text: Required\n'
    assert required_questions(snapshot) == set()
