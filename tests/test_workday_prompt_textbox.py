"""Search text is not a committed answer in textbox-shaped Workday prompts."""
from types import SimpleNamespace

import pytest

import config
import page_agent
import safety
from browser_automation import JobApplicationAssistant


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    page = context.new_page()
    yield page
    context.close()


def agent():
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    return page_agent.PageAgent(assistant, SimpleNamespace(), SimpleNamespace(auto_submit=False),
                               config.UserProfile(full_name="Example Candidate"),
                               SimpleNamespace(raw_text=""), SimpleNamespace(url="https://jobs.example.com"))


def widget(marker, offered="Referral network"):
    return f'''<label for="arbitrary-field">Application source *</label>
    <div data-automation-id="multiSelectContainer">
      <div data-automation-id="monikerSearchBox">
        <input id="arbitrary-field" {marker} data-uxi-multiselect-id="picker-1"
          oninput="document.getElementById('choices').hidden=false"
          onkeydown="if(event.key==='Enter')document.body.dataset.enter='yes'">
      </div>
      <span data-automation-id="selectedItem"></span>
      <div id="choices" hidden>
        <div role="option" data-automation-id="promptOption" data-automation-label="{offered}"
          onclick="document.querySelector('[data-automation-id=selectedItem]').textContent=this.textContent;
                   document.getElementById('arbitrary-field').value='';this.parentElement.hidden=true">{offered}</div>
      </div>
    </div>'''


@pytest.mark.parametrize("marker", ['data-uxi-widget-type="selectinput"', 'data-automation-id="searchBox"'])
@pytest.mark.parametrize("action", ["fill", "choose"])
@pytest.mark.parametrize("embedded", [False, True])
def test_textbox_shaped_prompt_commits_the_matching_option(page, marker, action, embedded):
    if embedded:
        page.set_content('<iframe title="Application form"></iframe>')
        target = page.frames[1]
    else:
        target = page
    target.set_content(widget(marker))
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role == "textbox")
    assert a.do(page, page_agent.Answer(control.ref, control.question, action, "Referral network", "profile"), control)
    assert target.locator('[data-automation-id=selectedItem]').inner_text() == "Referral network"
    assert target.locator("input").input_value() == ""
    assert target.evaluate("document.body.dataset.enter") is None


def test_prompt_without_the_requested_option_is_not_filled_with_search_text(page):
    page.set_content(widget('data-uxi-widget-type="selectinput"', offered="Other source"))
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role == "textbox")
    assert not a.do(page, page_agent.Answer(control.ref, control.question, "fill", "Referral network", "profile"), control)
    assert page.locator('[data-automation-id=selectedItem]').inner_text() == ""
    assert page.evaluate("document.body.dataset.enter") is None


def test_plain_search_textbox_is_still_a_text_field(page):
    page.set_content('<label for="x">Application source</label><input id="x" placeholder="Search">')
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role == "textbox")
    assert a.do(page, page_agent.Answer(control.ref, control.question, "fill", "Referral network", "profile"), control)
    assert page.locator("input").input_value() == "Referral network"


def test_school_search_enter_fetches_results_but_does_not_choose_first_row(page):
    page.set_content('''<fieldset aria-label="Education"><p>Type the name of your school or university and press Enter to search.</p>
      <label for="education-0--school">School or University</label>
      <div data-automation-id="multiSelectContainer"><div data-automation-id="monikerSearchBox">
        <input id="education-0--school" data-uxi-widget-type="selectinput" data-uxi-multiselect-id="school-picker"
          onkeydown="if(event.key==='Enter'){window.schoolSearch();event.preventDefault();}">
      </div><span data-automation-id="selectedItem"></span>
      <div id="school-results" data-automation-id="responsiveMonikerPrompt" data-associated-widget="school-picker"></div>
      </div></fieldset><script>window.schoolSearch=()=>{
        document.getElementById('school-results').innerHTML='<div data-automation-id="promptOption" data-automation-label="Different University">Different University</div><div data-automation-id="promptOption" data-automation-label="Example University">Example University</div>';
        const row=document.querySelector('[data-automation-label="Example University"]');
        row.onclick=()=>{document.querySelector('[data-automation-id=selectedItem]').textContent=row.textContent;document.querySelector('input').value='';row.parentElement.hidden=true;};
      };</script>''')
    a=agent()
    control=next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role=="textbox")
    assert a.do(page,page_agent.Answer(control.ref,control.question,"fill","Example University","profile.education"),control)
    assert page.locator('[data-automation-id=selectedItem]').inner_text()=="Example University"


@pytest.mark.parametrize("offered", ["Referral network", "Other source"])
def test_hierarchical_prompt_searches_branches_without_selecting_categories(page, offered):
    page.set_content(widget('data-uxi-widget-type="selectinput"', offered="Unused"))
    page.evaluate("""offered => {
      const choices=document.getElementById('choices');
      choices.setAttribute('data-automation-id','responsiveMonikerPrompt');
      choices.setAttribute('data-associated-widget','picker-1');
      window.root=()=>{choices.innerHTML='<div data-uxi-multiselectlistitem-type="2"><div data-automation-id="promptOption" data-automation-label="Category" onclick="window.children()">Category</div></div>';};
      window.children=()=>{
        choices.innerHTML='<button data-automation-id="backButton">Back</button><div data-uxi-multiselectlistitem-type="1"><div data-automation-id="promptOption"></div></div>';
        choices.querySelector('button').onclick=window.root;
        const leaf=choices.querySelector('[data-automation-id=promptOption]');
        leaf.textContent=offered; leaf.setAttribute('data-automation-label',offered);
        leaf.onclick=()=>{document.querySelector('[data-automation-id=selectedItem]').textContent=offered;document.querySelector('input').value='';choices.hidden=true;};
      };
      window.root();
    }""", offered)
    a=agent()
    control=next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role=="textbox")
    committed=a.do(page,page_agent.Answer(control.ref,control.question,"fill","Referral network","profile"),control)
    assert committed == (offered=="Referral network")
    assert page.locator('[data-automation-id=selectedItem]').inner_text()==("Referral network" if committed else "")
    assert page.evaluate("document.body.dataset.enter") is None


@pytest.mark.parametrize("extra, expected", [("Other source", True), ("LinkedIn Learning", False)])
@pytest.mark.parametrize("position", [1, 19])
def test_nested_virtual_prompt_reads_all_leaf_options_before_matching(page, extra, expected, position):
    page.set_content(widget('data-uxi-widget-type="selectinput"'))
    page.evaluate("""([extra, position]) => {
      const choices=document.getElementById('choices');
      choices.setAttribute('data-automation-id','responsiveMonikerPrompt');
      choices.setAttribute('data-associated-widget','picker-1');
      window.root=()=>{choices.innerHTML='<div data-uxi-multiselectlistitem-type="2"><div data-automation-id="promptOption" data-automation-label="Source channels" onclick="window.children()">Source channels</div></div>';};
      window.children=()=>{
        choices.innerHTML='<button data-automation-id="backButton">Back</button><div data-automation-id="activeListContainer" style="height:80px;overflow:auto"><div style="position:relative;height:800px"></div></div>';
        choices.querySelector('button').onclick=window.root;
        const list=choices.querySelector('[data-automation-id=activeListContainer]');
        const items=Array.from({length:25},(_,i)=>i===position?'LinkedIn.com':i===23?extra:'Source '+i);
        const draw=()=>{
          const start=Math.floor(list.scrollTop/32), content=list.firstElementChild;content.innerHTML='';
          items.slice(start,start+4).forEach((label,i)=>{
            const node=document.createElement('div');node.setAttribute('data-uxi-multiselectlistitem-type','1');
            node.style.cssText='position:absolute;height:32px;top:'+((start+i)*32)+'px';
            const row=document.createElement('div');row.setAttribute('data-automation-id','promptOption');row.setAttribute('data-automation-label',label);row.textContent=label;
            row.onclick=()=>{document.querySelector('[data-automation-id=selectedItem]').textContent=label;document.querySelector('input').value='';choices.hidden=true;};
            node.appendChild(row);content.appendChild(node);
          });
        };list.onscroll=draw;draw();
      };window.root();
    }""", [extra, position])
    a=agent()
    control=next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role=="textbox")
    assert a.do(page,page_agent.Answer(control.ref,control.question,"fill","LinkedIn","profile"),control) is expected
    assert page.locator('[data-automation-id=selectedItem]').inner_text()==("LinkedIn.com" if expected else "")


def test_school_search_enter_without_instruction_prose_and_other_fallback(page):
    """CHAGEE Workday (f281): --school with enterkeyhint='search' shows 'No Items.' until Enter is pressed,
    and falls back to 'Other' when the institution is not listed and the section instructs selecting 'Other'."""
    page.set_content('''<fieldset aria-label="Education">
      <p><b>Instructions: Enter your highest level of education. If your institution is not listed, please select “Other.”</b></p>
      <label for="education-0--school">School or University</label>
      <div data-automation-id="multiSelectContainer"><div data-automation-id="monikerSearchBox">
        <input id="education-0--school" enterkeyhint="search" data-uxi-widget-type="selectinput" data-uxi-multiselect-id="school-picker"
          onkeydown="if(event.key==='Enter'){window.schoolSearch(this.value);event.preventDefault();}">
      </div><span data-automation-id="selectedItem"></span>
      <div id="school-results" data-automation-id="responsiveMonikerPrompt" data-associated-widget="school-picker">
        <div data-automation-id="promptOption">No Items.</div>
      </div></div></fieldset><script>window.schoolSearch=(query)=>{
        const box=document.getElementById('school-results');
        if(/other/i.test(query)){
          box.innerHTML='<div data-automation-id="promptOption" data-automation-label="Other">Other</div>';
          const row=box.querySelector('[data-automation-label="Other"]');
          row.onclick=()=>{document.querySelector('[data-automation-id=selectedItem]').textContent='Other';document.querySelector('input').value='';box.hidden=true;};
        } else {
          box.innerHTML='<div data-automation-id="promptOption">No Items.</div>';
        }
      };</script>''')
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role == "textbox")
    assert a.do(page, page_agent.Answer(control.ref, control.question, "fill", "JNTU Hyderabad", "profile.education"), control)
    assert page.locator('[data-automation-id=selectedItem]').inner_text() == "Other"
    assert page.locator("input").input_value() == ""


def test_school_search_cannot_find_search_other_fallback(page):
    """HonorHealth Workday (f282): 'If you cannot find your school in the list, please search \"other\" and press enter.'
    broadens _school_names fallback regex beyond 'not listed' so 'Other' is queried when the school is absent."""
    page.set_content('''<div role="group" aria-label="Education">
      <p>How to Enter a School: If you cannot find your school in the list, please search "<b>other</b>" and press enter.</p>
      <label for="education-1--school">School or University*</label>
      <div class="css-ujzx5o">
        <input id="education-1--school" enterkeyhint="search" placeholder="Search"
          onkeydown="if(event.key==='Enter'){window.schoolSearch(this.value);event.preventDefault();}">
        <div aria-hidden="true">0 items selected</div>
      </div>
      <div id="school-results" hidden></div>
    </div><script>window.schoolSearch=(query)=>{
      const box=document.getElementById('school-results');
      box.hidden=false;
      if(/other/i.test(query)){
        box.innerHTML='<div role="option" data-automation-label="Other">Other</div>';
        const row=box.querySelector('[role="option"]');
        row.onclick=()=>{
          document.querySelector('[aria-hidden="true"]').textContent='1 item selected, Other';
          document.getElementById('education-1--school').value='';
          box.hidden=true;
        };
      } else {
        box.innerHTML='<div role="option">No Items.</div>';
      }
    };</script>''')
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role == "textbox")
    assert a.do(page, page_agent.Answer(control.ref, control.question, "fill", "JNTU Hyderabad", "profile.education"), control)
    assert "Other" in page.locator('[aria-hidden="true"]').inner_text()
    assert page.locator("input").input_value() == ""


def test_modern_workday_skills_enter_and_option_selection(page):
    """HonorHealth Workday (f282): Modern Canvas-style skills input lacks data-automation-id and data-uxi-widget-type,
    has enterkeyhint='search', requires Enter to query options, and registers selection in items selected list."""
    page.set_content('''<div>
      <h4>Skills</h4>
      <p>You must press ENTER for skills to populate.</p>
      <label for="skills--skills">Type to Add Skills*</label>
      <div class="css-container">
        <div class="css-ujzx5o">
          <input id="skills--skills" enterkeyhint="search" placeholder="Search"
            onkeydown="if(event.key==='Enter'){window.searchSkills(this.value);event.preventDefault();}">
          <div aria-hidden="true" class="selected-summary">0 items selected</div>
        </div>
        <ul role="listbox" aria-label="items selected"></ul>
        <div id="results" hidden></div>
      </div>
    </div><script>window.searchSkills=(query)=>{
      const box=document.getElementById('results');
      box.hidden=false;
      box.innerHTML='<div role="option">Python</div><div role="option">Network Security</div>';
      box.querySelectorAll('[role="option"]').forEach(opt=>{
        opt.onclick=()=>{
          const sel=document.querySelector('ul[role="listbox"]');
          const li=document.createElement('li');
          li.setAttribute('role','option');
          li.textContent=opt.textContent+', press delete to clear value.';
          sel.appendChild(li);
          document.querySelector('.selected-summary').textContent='1 item selected, '+opt.textContent;
          document.getElementById('skills--skills').value='';
          box.hidden=true;
        };
      });
    };</script>''')
    a = agent()
    control = next(c for c in page_agent.parse_snapshot(a.snapshot(page)) if c.role == "textbox")
    assert a.do(page, page_agent.Answer(control.ref, control.question, "fill", "Python, Network Security", "profile.skills"), control)
    assert "Python" in page.locator('ul[role="listbox"]').inner_text()
