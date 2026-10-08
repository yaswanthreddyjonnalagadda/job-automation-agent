import pytest
from playwright.sync_api import sync_playwright

import diagnostics as d
from test_diagnostic_sanitization import SENTINELS, assert_private


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    page = context.new_page()
    yield page
    context.close()


def test_masking_preserves_values_events_and_restores_page(page, tmp_path, monkeypatch):
    page.set_content('<label>Email</label><input value="Privacy Test Person">'
                     '<input type=password value="SecretPassword!123">'
                     '<textarea>482193</textarea><div contenteditable>123 Privacy Sentinel Street</div>'
                     '<span>SENSITIVE_APPLICATION_ANSWER_SENTINEL</span><button>Continue</button>')
    page.evaluate("window.events=[]; document.addEventListener('input',()=>events.push('input')); document.addEventListener('change',()=>events.push('change'))")
    before = page.locator('input').evaluate_all('(els)=>els.map(e=>e.value)')
    original = page.screenshot
    def checked_capture(**kwargs):
        assert page.locator('input').first.evaluate('(e)=>getComputedStyle(e).visibility') == 'hidden'
        assert page.locator('span').evaluate('(e)=>getComputedStyle(e).color') == 'rgba(0, 0, 0, 0)'
        return original(**kwargs)
    monkeypatch.setattr(page, 'screenshot', checked_capture)
    assert d.capture_safe_screenshot(page, tmp_path / 'page.png')
    assert page.locator('input').evaluate_all('(els)=>els.map(e=>e.value)') == before
    assert page.evaluate('events') == []
    assert page.locator('input').first.is_visible()
    assert page.locator('style[data-diagnostic-mask]').count() == 0
    page.locator('input').first.fill('still works')
    assert page.locator('input').first.input_value() == 'still works'


def test_capture_failure_restores_and_does_not_mark_safe(page, tmp_path, monkeypatch):
    page.set_content('<input value="SecretPassword!123">')
    monkeypatch.setattr(page, 'screenshot', lambda **k: (_ for _ in ()).throw(RuntimeError('482193')))
    assert not d.capture_safe_screenshot(page, tmp_path / 'page.png')
    assert page.locator('input').is_visible()
    assert page.locator('input').input_value() == 'SecretPassword!123'
    assert not d.is_safe_artifact(tmp_path / 'page.png')


def test_mask_install_failure_skips_capture(page, tmp_path, monkeypatch):
    monkeypatch.setattr(page, 'evaluate', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('482193')))
    monkeypatch.setattr(page, 'screenshot', lambda **k: pytest.fail('raw capture attempted'))
    assert not d.capture_safe_screenshot(page, tmp_path / 'page.png')


def test_central_capture_sentinels_absent_from_all_text_artifacts(page, tmp_path):
    page.set_content('<h1>Application</h1><label>Email</label><input value="'+SENTINELS[1]+'">'
                     '<textarea>'+SENTINELS[7]+'</textarea><div contenteditable>'+SENTINELS[3]+'</div>'
                     '<input type=hidden value="'+SENTINELS[5]+'"><script>window.secret="'+SENTINELS[6]+'"</script>'
                     '<p>'+' '.join(SENTINELS)+'</p>')
    result = d.capture_bundle(page, tmp_path, reason=' '.join(SENTINELS), console_logs=[{'text':' '.join(SENTINELS)}])
    assert result.get('html') and result.get('screenshot')
    for path in tmp_path.rglob('*'):
        if path.is_file() and path.suffix != '.png':
            assert_private(path.read_text())


def test_screenshot_pixels_are_conservatively_covered_including_documents_and_frames(page, tmp_path):
    import base64
    page.set_content('<style>p::first-letter {color:red!important; -webkit-text-fill-color:red!important}</style>'
                     '<p>Privacy Test Person</p><canvas></canvas><iframe srcdoc="SecretPassword!123"></iframe>'
                     '<div id="host"></div><div style="position:absolute;top:1100px">482193</div>')
    page.evaluate("document.querySelector('#host').attachShadow({mode:'closed'}).innerHTML='<p>SENSITIVE_RESUME_CONTENT_SENTINEL</p>'")
    path = tmp_path/'page.png'
    assert d.capture_safe_screenshot(page,path)
    uri = 'data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode()
    colors = page.evaluate('''async uri => {
        const img=new Image(); img.src=uri; await img.decode();
        const canvas=document.createElement('canvas'); canvas.width=img.width;canvas.height=img.height;
        const ctx=canvas.getContext('2d'); ctx.drawImage(img,0,0);
        const pixels=ctx.getImageData(0,0,img.width,img.height).data;
        const colors=new Set(); for(let i=0;i<pixels.length;i+=4) colors.add([...pixels.slice(i,i+4)].join(','));
        return [...colors];
    }''',uri)
    assert colors == ['32,33,36,255']
    assert page.locator('[data-diagnostic-mask]').count() == 0


def test_real_account_state_writer_keeps_safe_artifacts_for_lowercase_states(page,tmp_path):
    from types import SimpleNamespace
    import account_state
    import page_agent
    page.set_content('<label>Email</label><input value="privacy-test@example.invalid">'
                     '<input type=password value="SecretPassword!123">')
    agent = page_agent.PageAgent.__new__(page_agent.PageAgent)
    agent._account_seen = {}
    agent.job_dir = tmp_path
    agent.snapshot = lambda _: page.locator('body').aria_snapshot(mode='ai')
    agent._note_account_state(page,'example.invalid',account_state.AccountState(account_state.SIGN_IN_FORM),
                              SimpleNamespace(action='sign_in',why='SecretPassword!123'))
    artifacts = list((tmp_path/'account').glob('*.txt')) + list((tmp_path/'account').glob('*.png'))
    assert len(artifacts) == 2 and all(d.is_safe_artifact(p) for p in artifacts)
    assert_private(next(p for p in artifacts if p.suffix == '.txt').read_text())
