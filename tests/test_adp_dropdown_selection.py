"""ADP async shadow-DOM selects require their own filter and committed value."""
import pytest

from sites.adp import AdpAdapter
from test_workday_prompt_textbox import agent, browser, page  # noqa: F401

LOADING = '''- button "Step Status current Equal Employment Opportunity Information" [ref=e1]
- generic "Equal Employment Opportunity Information" [active] [ref=e2]
- button "Next" [disabled]
'''


def test_adp_transition_waits_for_current_step_content(monkeypatch):
    from types import SimpleNamespace
    import sites
    a=agent()
    loaded='- textbox "Application question" [ref=e3]'
    snapshots=iter([LOADING,loaded])
    monkeypatch.setattr(a,'snapshot',lambda page:next(snapshots))
    monkeypatch.setattr(sites,'adapter_for',lambda url:AdpAdapter())
    fake_page=SimpleNamespace(url='https://myjobs.adp.com/employer/cx/app',wait_for_timeout=lambda ms:None)
    assert a.read_when_loaded(fake_page) == loaded


@pytest.mark.parametrize('snapshot', [
    '- button "Next" [disabled]',
    LOADING.replace('- button "Next" [disabled]', '- button "Next" [ref=e4]'),
    LOADING.replace('[ref=e2]', '[ref=e2]:\n  - textbox "Question" [ref=e3]'),
])
def test_disabled_navigation_alone_does_not_establish_a_loading_step(snapshot):
    assert not AdpAdapter().form_loading(snapshot)


def widget(page):
    page.set_content('''<sdf-select-simple filterasync></sdf-select-simple><script>
    const host=document.querySelector('sdf-select-simple');
    const root=host.attachShadow({mode:'open'});
    root.innerHTML=`<span id="label">State</span><div role="button" tabindex="0"
      aria-labelledby="label value" aria-expanded="false"><span id="value">Select...</span></div>
      <div id="menu" hidden><input part="filter-input" aria-label="Filter options"><div role="listbox"></div></div>`;
    const trigger=root.querySelector('[role=button]');
    trigger.onclick=()=>{root.querySelector('#menu').hidden=false;trigger.setAttribute('aria-expanded','true');};
    root.querySelector('input').oninput=()=>{
      const list=root.querySelector('[role=listbox]');list.replaceChildren();
      for(const label of ['West Virginia','Virginia']) {
        const row=document.createElement('div');row.setAttribute('role','option');row.textContent=label;
        row.onclick=()=>{root.querySelector('#value').textContent=label;root.querySelector('#menu').hidden=true;trigger.setAttribute('aria-expanded','false');};
        list.append(row);
      }
    };
    </script>''')
    return page.locator('sdf-select-simple [role="button"]')


@pytest.mark.parametrize('value,expected', [('Virginia',True),('Missing answer',False)])
def test_async_select_uses_its_filter_and_only_commits_a_matching_option(page, value, expected):
    control=widget(page)
    a=agent()
    handled=AdpAdapter().choose_dropdown(page,control,value,
        pick=lambda labels:a._pick_label(labels,value),click=a.assistant._click_resiliently)
    assert handled is expected
    assert page.locator('sdf-select-simple').evaluate('(el)=>el.shadowRoot.querySelector("#value").textContent') == (
        value if expected else 'Select...')


def test_adp_select_click_denial_does_not_fill_or_choose(page):
    control=widget(page)
    assert AdpAdapter().choose_dropdown(page,control,'Virginia',pick=lambda labels:0,
                                       click=lambda *args,**kwargs:False) is False
    assert page.locator('sdf-select-simple').evaluate('(el)=>el.shadowRoot.querySelector("input").value') == ''


def test_an_offered_phone_alias_is_matched_before_filtering(page):
    control=widget(page)
    page.locator('sdf-select-simple').evaluate('''el=>{
      const root=el.shadowRoot, list=root.querySelector('[role=listbox]');
      const row=document.createElement('div');row.setAttribute('role','option');row.textContent='Cell';
      row.onclick=()=>{root.querySelector('#value').textContent='Cell';root.querySelector('#menu').hidden=true;root.querySelector('[role=button]').setAttribute('aria-expanded','false');};
      list.append(row);
      root.querySelector('input').oninput=()=>list.replaceChildren();
    }''')
    a=agent()
    assert AdpAdapter().choose_dropdown(page,control,'Mobile',
        pick=lambda labels:a._pick_label(labels,'Mobile'),click=a.assistant._click_resiliently) is True
    assert control.inner_text() == 'Cell'
    assert page.locator('sdf-select-simple').evaluate('(el)=>el.shadowRoot.querySelector("input").value') == ''


def test_adp_hook_leaves_unrelated_buttons_to_generic_logic(page):
    page.set_content('<button type="button">Next</button>')
    assert AdpAdapter().choose_dropdown(page,page.get_by_role('button'),'Virginia',
                                       pick=lambda labels:0,click=lambda *args,**kwargs:True) is None
