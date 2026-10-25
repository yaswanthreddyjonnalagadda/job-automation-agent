import logging

import diagnostics
import local_debug


def test_local_logging_is_opt_in_and_preserves_answers_not_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(local_debug, 'ROOT', tmp_path)
    monkeypatch.delenv('LOCAL_DEBUG_CAPTURE', raising=False)
    record = logging.LogRecord('test', logging.INFO, __file__, 1,
                               'Applicant %s salary %s password=%s',
                               ('person@example.test', '125000', 'private-password'), None)
    local_debug.record_log(record)
    assert not list(tmp_path.iterdir())
    monkeypatch.setenv('LOCAL_DEBUG_CAPTURE', '1')
    local_debug.record_log(record)
    text = next(tmp_path.glob('*.log')).read_text()
    assert 'person@example.test' in text and '125000' in text
    assert 'private-password' not in text
    assert record.args  # Local capture does not mutate the shared record.


def test_private_capture_is_not_exportable_and_keeps_field_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(local_debug, 'ROOT', tmp_path)
    monkeypatch.setenv('LOCAL_DEBUG_CAPTURE', '1')
    class Body:
        def aria_snapshot(self, **kwargs):
            return '- textbox "Email": person@example.test\n- textbox "Password": private-password'
    class Page:
        frames = []
        url = 'https://example.test/apply?token=private-token'
        def locator(self, selector):
            return Body()
        def screenshot(self, **kwargs):
            assert kwargs['mask']
            return b'private screenshot'
        def content(self):
            return '<label>Email</label><input value="person@example.test"><input type="password" value="private-password"><script>private-token</script>'
    local_debug.capture(Page(), 'page_01')
    folder = next(p for p in tmp_path.iterdir() if p.is_dir())
    assert 'person@example.test' in (folder / 'page.txt').read_text()
    assert 'private-password' not in (folder / 'page.txt').read_text()
    assert 'person@example.test' in (folder / 'page.html').read_text()
    assert 'private-password' not in (folder / 'page.html').read_text()
    assert 'private-token' not in (folder / 'page.html').read_text()
    assert not diagnostics.is_safe_artifact(folder / 'page.png')


def test_secret_redaction_preserves_salary_and_phone(monkeypatch):
    monkeypatch.setenv('ATS_PASSWORD', 'configured-secret')
    text = local_debug.redact_secrets('125000 +1 212 555 0100 configured-secret OTP=482193')
    assert '125000' in text and '+1 212 555 0100' in text
    assert 'configured-secret' not in text and '482193' not in text


def test_browser_capture_keeps_form_readable_without_changing_values(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright
    monkeypatch.setattr(local_debug, 'ROOT', tmp_path)
    monkeypatch.setenv('LOCAL_DEBUG_CAPTURE', '1')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('<label>Email<input value="person@example.test"></label>'
                             '<label>Password<input type="password" value="private-password"></label>'
                             '<label>Verification code<input value="482193"></label>'
                             '<label>Country<select><option selected>Example Country</option></select></label>')
            page.evaluate("window.changes=0; document.addEventListener('input',()=>changes++)")
            local_debug.capture(page, 'synthetic form')
            folder = next(p for p in tmp_path.iterdir() if p.is_dir())
            assert json_load(folder / 'capture.json')['errors'] == []
            assert (folder / 'page.png').stat().st_size > 1000
            assert 'Example Country' in (folder / 'page.txt').read_text()
            assert 'person@example.test' in (folder / 'page.html').read_text()
            assert 'private-password' not in (folder / 'page.html').read_text()
            assert '482193' not in (folder / 'page.html').read_text()
            assert page.get_by_label('Password').input_value() == 'private-password'
            assert page.evaluate('changes') == 0
        finally:
            browser.close()


def json_load(path):
    import json
    return json.loads(path.read_text())


def test_a_short_setting_named_like_a_secret_does_not_mangle_the_log(monkeypatch):
    monkeypatch.setenv('SESSION_FLAG', '1')
    monkeypatch.setenv('API_KEY_FOR_TEST', 'abcd1234efgh')
    text = local_debug.redact_secrets('salary 125000 key abcd1234efgh')
    assert '125000' in text and 'abcd1234efgh' not in text
