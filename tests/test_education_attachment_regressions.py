from types import SimpleNamespace
from pathlib import Path
import pytest
from test_workday_prompt_textbox import agent, browser, page
from sites.workday import WorkdayAdapter


@pytest.mark.parametrize("offered,expected", [("Primary subject","Primary subject"),("Approved substitute","Approved substitute"),("Other subject","")])
def test_education_never_commits_the_highlighted_unrelated_option(page, monkeypatch, offered, expected):
    page.set_content(f'''<div data-automation-id="multiSelectContainer">
      <input id="row--fieldOfStudy" data-uxi-multiselect-id="p" data-uxi-widget-type="selectinput"
        onkeydown="if(event.key==='Enter')document.querySelector('[data-automation-id=selectedItem]').textContent='Accounting'">
      <span data-automation-id="selectedItem"></span>
      <div data-automation-id="promptOption" data-automation-label="Accounting">Accounting</div>
      <div data-automation-id="promptOption" data-automation-label="{offered}"
        onclick="document.querySelector('[data-automation-id=selectedItem]').textContent=this.textContent;document.querySelector('input').value=''">{offered}</div>
    </div>''')
    a=agent().assistant
    a._profile=SimpleNamespace(answer_alternatives={"Primary subject":["Approved substitute"]})
    adapter=WorkdayAdapter()
    monkeypatch.setattr(adapter,"delete_all_entries",lambda *args:0)
    monkeypatch.setattr(adapter,"_ensure_entry_slot",lambda *args:None)
    adapter.fill_education_section(a,page,[{"field":"Primary subject"}])
    assert page.locator('[data-automation-id=selectedItem]').inner_text()==expected


@pytest.mark.parametrize("embedded",[False,True])
def test_resume_listed_by_previous_run_is_not_uploaded_again(page,tmp_path,embedded):
    resume=tmp_path/'Example_Resume.pdf';resume.write_bytes(b'%PDF-1.4 example')
    if embedded:
        page.set_content('<iframe title="Application"></iframe>');target=page.frames[1]
    else:
        target=page
    target.set_content(f'<fieldset><legend>Resume</legend><div>{resume.name}</div><button>Delete {resume.name}</button><input type="file" aria-label="Upload resume" onchange="document.body.dataset.uploaded=\'yes\'"></fieldset>')
    a=agent();a.resume_file=resume
    assert not a._attach_resume_to_its_input(page)
    assert target.evaluate('document.body.dataset.uploaded') is None
    assert target.locator('input').evaluate('e=>e.files.length')==0


def test_unattached_resume_is_uploaded_once(page,tmp_path):
    resume=tmp_path/'Example_Resume.pdf';resume.write_bytes(b'%PDF-1.4 example')
    page.set_content('<fieldset><legend>Resume</legend><input type="file" aria-label="Upload resume"></fieldset>')
    a=agent();a.resume_file=resume
    assert a._attach_resume_to_its_input(page)
    assert not a._attach_resume_to_its_input(page)
    assert page.locator('input').evaluate('e=>e.files.length')==1



@pytest.mark.parametrize("offered",["Primary subject","Approved substitute"])
def test_virtual_study_option_is_found_beyond_the_first_rows(page,offered):
    page.set_content('''<div data-automation-id="multiSelectContainer">
      <input id="row--fieldOfStudy" data-uxi-multiselect-id="p"><span data-automation-id="selectedItem"></span>
    </div><div data-automation-id="responsiveMonikerPrompt" data-associated-widget="p">
      <div data-automation-id="activeListContainer" style="height:50px;overflow:auto;position:relative">
        <div style="height:800px"><div id="row" data-automation-id="promptOption" data-automation-label="Accounting" style="position:absolute;top:0">Accounting</div></div>
      </div></div>''')
    page.evaluate("""offered=>{
      const list=document.querySelector('[data-automation-id=activeListContainer]');
      const row=document.getElementById('row');
      list.onscroll=()=>{const label=list.scrollTop>100?offered:'Accounting';row.textContent=label;row.setAttribute('data-automation-label',label);row.style.top=list.scrollTop+'px';};
      row.onclick=()=>{document.querySelector('[data-automation-id=selectedItem]').textContent=row.textContent;document.querySelector('input').value='';};
    }""",offered)
    a=agent().assistant
    assert WorkdayAdapter().select_from_searchable_input(a,page,'--fieldOfStudy',['Primary subject','Approved substitute'],keyboard=False)
    assert page.locator('[data-automation-id=selectedItem]').inner_text()==offered


def test_wrong_committed_chip_is_not_reported_as_the_requested_subject(page):
    page.set_content('''<div data-automation-id="multiSelectContainer">
      <input id="row--fieldOfStudy" data-uxi-multiselect-id="p"><span data-automation-id="selectedItem"></span>
      <div data-automation-id="promptOption" data-automation-label="Primary subject"
       onclick="document.querySelector('[data-automation-id=selectedItem]').textContent='Accounting'">Primary subject</div></div>''')
    assert not WorkdayAdapter().select_from_searchable_input(agent().assistant,page,'--fieldOfStudy',['Primary subject'],keyboard=False)



def test_explicit_profile_subject_overrides_resume_extraction():
    import repeated_entries
    profile=SimpleNamespace(education=[("Bachelor's","Approved discipline","Example University","2020")],education_dates=[])
    history={"education":[{"school":"Example University","degree":"Bachelor of Science","field":"Earlier discipline"}]}
    actual=repeated_entries.with_profile(history,profile)
    assert actual['education'][0]['field']=='Approved discipline'
    assert history['education'][0]['field']=='Earlier discipline'



def test_relocated_prompt_keeps_the_identity_of_its_education_row(page):
    page.set_content('''<div data-automation-id="multiSelectContainer" data-uxi-element-id="p">
      <input id="a--fieldOfStudy" data-uxi-multiselect-id="p" onclick="document.getElementById('floating').appendChild(this)"
        oninput="document.getElementById('option').textContent='Primary subject';document.getElementById('option').setAttribute('data-automation-label','Primary subject')">
      <span data-automation-id="selectedItem"></span></div>
      <div data-automation-id="multiSelectContainer"><input id="b--fieldOfStudy" data-uxi-multiselect-id="q"></div>
      <div id="floating"></div>
      <div data-automation-id="responsiveMonikerPrompt" data-associated-widget="p">
        <div id="option" data-automation-id="promptOption" data-automation-label="Accounting"
         onclick="document.querySelector('[data-automation-id=selectedItem]').textContent=this.textContent;document.getElementById('a--fieldOfStudy').value=''">Accounting</div></div>''')
    assert WorkdayAdapter().select_from_searchable_input(agent().assistant,page,'--fieldOfStudy',['Primary subject'],keyboard=False)
    assert page.locator('[data-automation-id=selectedItem]').inner_text()=='Primary subject'
    assert page.locator('#b--fieldOfStudy').input_value()==''
