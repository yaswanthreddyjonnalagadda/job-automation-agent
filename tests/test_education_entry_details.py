from types import SimpleNamespace
import repeated_entries
import option_match
import pytest


@pytest.mark.parametrize('degree,label', [('Master of Science',"Master's Level Degree"),('Bachelor of Technology',"Bachelor's Level Degree")])
def test_explicit_degree_maps_to_the_offered_degree_level(degree,label):
    assert option_match.best_option(['Not Indicated',label],degree)==1


def test_school_facts_are_explicit_and_scoped_to_their_school():
    profile=SimpleNamespace(education_details=[dict(school='Example University',city='College Town',country='Example Country',graduated=True)])
    history=repeated_entries.with_profile({'education':[dict(school='Example University'),dict(school='Other College')]},profile)
    peers=[dict(school='Example University'),dict(school='Other College')]
    for label,value in [('City','College Town'),('Country Select...','Example Country'),('Graduated? Select...','Yes')]:
        assert repeated_entries.answer(repeated_entries.Entry('education',0,label,peers[0],peers),history)[0]==value
        assert repeated_entries.answer(repeated_entries.Entry('education',1,label,peers[1],peers),history)[0]==''
