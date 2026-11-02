"""Structured education remains usable when no resume history was extracted."""
from types import SimpleNamespace

import repeated_entries


def profile():
    return SimpleNamespace(
        education=[['Master\'s', 'Computer Technology', 'Example University', '2022'],
                   ['Bachelor\'s', 'Electrical Engineering', 'Other University', '2019']],
        education_dates=[['Example University', 'August 2021', 'December 2022'],
                         ['Other University', 'August 2015', 'April 2019']],
    )


def test_structured_degrees_exist_without_resume_extraction():
    result = repeated_entries.with_profile({}, profile())
    assert [row['school'] for row in result['education']] == ['Example University', 'Other University']
    assert result['education'][0]['field'] == 'Computer Technology'
    assert result['education'][0]['end'] == 'December 2022'
    assert result['education'][1]['start'] == 'August 2015'


def test_extracted_partial_degree_keeps_profile_fields_and_adds_missing_degree():
    result = repeated_entries.with_profile({'education': [
        {'school': 'Example University', 'field': 'Computer Science', 'degree': 'Master\'s'},
    ]}, profile())
    assert len(result['education']) == 2
    assert result['education'][0]['field'] == 'Computer Technology'


def test_two_degrees_at_one_school_are_not_collapsed():
    p=profile()
    p.education[1][2]='Example University'
    result=repeated_entries.with_profile({},p)
    assert len(result['education'])==2
    assert [row['field'] for row in result['education']]==['Computer Technology','Electrical Engineering']


def test_qualified_completion_group_gives_year_its_date_identity():
    snapshot = '''- group "Education":
  - heading "Education" [level=4]
  - group "Education 1":
    - heading "Education 1" [level=5]
    - textbox "School or University" [ref=s]: Example University
    - group "From":
      - spinbutton "Year" [ref=start]: "2021"
    - group "To (Actual or Expected)":
      - spinbutton "Year" [ref=end]
'''
    entry = repeated_entries.entry_map(snapshot)['end']
    value, _, blank = repeated_entries.answer(entry, repeated_entries.with_profile({}, profile()))
    assert value == '2022' and not blank
