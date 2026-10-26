"""Approved per-employer reasons and contact permissions remain scoped."""
from types import SimpleNamespace
import repeated_entries


def test_profile_employer_preferences_are_applied_to_the_matching_record_only():
    history={'experience':[{'company':'Acme','current':True},{'company':'Globex','current':False}]}
    profile=SimpleNamespace(reasons_for_leaving=(('Acme','Contract ending'),('Globex','Contract ended')),
        employer_contact_permissions=(('Acme','No'),))
    resolved=repeated_entries.with_profile(history,profile)
    peers=[{'employer':'Acme'},{'employer':'Globex'}]
    def field(index,label):
        return repeated_entries.Entry('work',index,label,values=peers[index],peers=peers)
    for index,expected in [(0,'Contract ending'),(1,'Contract ended')]:
        entry=field(index,'Reason for Leaving or Looking to Leave')
        assert repeated_entries.answer(entry,resolved)[0] == expected
    assert repeated_entries.answer(field(0,'May We Contact?'),resolved)[0] == 'No'
    assert repeated_entries.answer(field(1,'May We Contact?'),resolved)[0] == ''
    assert 'may_contact' not in history['experience'][0]
