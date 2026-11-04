"""A school that is not in Workday's list is entered as "Other", whatever words the tenant uses to say so (f332).

Analog Devices (3 November): the Education 2 school (JNTU Hyderabad) is not in the list, and the section says
'If your institution does not appear in the pre-populated list ... type "Other," press Enter, and then select the
country'. The agent looked for "Other" only after the wordings 'not listed' (f281) and 'cannot find' (f282), so a third
wording sent it searching for JNTU alone for a minute, three times, and the Degree and Field of Study after it failed
with the School box still open. The rule is now about what the text asks for -- type, search or select a quoted
"Other" -- not about which sentence leads up to it.
"""
import pytest

from sites import workday
from sites.workday import WorkdayAdapter, education_text_offers_other

OFFERS = [
    'To enter your university or school, type the name of your institution in the text box, and press Enter. If your '
    'institution does not appear in the pre-populated list, or if your highest degree is High School Diploma, type '
    '“Other,” press Enter, and then select the appropriate country.',
    'Instructions: Enter your highest level of education. If your institution is not listed, please select “Other.”',
    'How to Enter a School: If you cannot find your school in the list, please search "other" and press enter.',
    "Schools missing from the catalogue can be entered by choosing 'Other' from the list.",
    'No match for your school? Search for “Other” and pick it.',
    'Your school is absent from our list: use "Other" instead.',
    'If you went to a school we do not list, type ‘Other’ and press Enter.',
]

DOES_NOT = [
    'Enter your highest level of education.',
    'To enter your university or school, type the name of your institution in the text box, and press Enter.',
    'Select your degree. Other qualifications can be added under Certifications.',
    '',
]


@pytest.mark.parametrize("text", OFFERS)
def test_every_wording_that_points_to_other_adds_it(text):
    assert education_text_offers_other(text)


@pytest.mark.parametrize("text", DOES_NOT)
def test_text_that_does_not_point_to_other_does_not(text):
    assert not education_text_offers_other(text)


class Group:
    def __init__(self, text):
        self.text = text

    def count(self):
        return 1

    def inner_text(self, timeout=0):
        return self.text


class FakePage:
    def __init__(self, text):
        self.text = text

    def get_by_role(self, role, name=None, exact=False):
        return Group(self.text)


class Assistant:
    _profile = None


def test_the_school_names_end_with_other_for_the_analog_devices_wording():
    names = WorkdayAdapter._school_names(Assistant(), "JNTU Hyderabad", page=FakePage(OFFERS[0]))
    assert names == ["JNTU Hyderabad", "Other"]


def test_the_school_names_stay_alone_when_the_text_does_not_offer_other():
    names = WorkdayAdapter._school_names(Assistant(), "JNTU Hyderabad", page=FakePage(DOES_NOT[1]))
    assert names == ["JNTU Hyderabad"]


def test_the_module_has_one_rule_for_it():
    source = open(workday.__file__, encoding="utf-8").read()
    assert source.count("not\s+listed") <= 1, "the wording list must live in education_text_offers_other only"
