"""Resume uploads must use compatible slots and honor explicit rejections."""
from types import SimpleNamespace
import pytest
from browser_automation import JobApplicationAssistant

@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        yield page
        browser.close()

@pytest.mark.parametrize("markup,expected,selected", [
    ('<input id="video" aria-label="Upload Video" type=file accept="video/mp4,video/*">', False, None),
    ('<input id="cover" name="cover_letter" type=file accept=".pdf">', False, None),
    ('<input id="video" type=file accept="video/*"><input id="resume" type=file accept=".pdf,.docx">', True, 'resume'),
    ('<input id="generic" type=file>', True, 'generic'),
    ('<input id="resume" type=file accept="application/pdf">', True, 'resume'),
    ('<input id="resume" type=file onchange="document.querySelector(&quot;[role=alert]&quot;).textContent=&quot;Invalid media type&quot;"><div role="alert"></div>', False, 'resume'),
])
def test_upload_slot_and_result(page, tmp_path, monkeypatch, markup, expected, selected):
    monkeypatch.chdir(tmp_path)
    document = tmp_path / 'resume.pdf'
    document.write_bytes(b'%PDF-1.4 synthetic resume')
    page.set_content(markup)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant._config = SimpleNamespace(resume_source="tailored")
    assistant._file_already_attached = lambda p, name: False
    assistant.adapter = lambda p: SimpleNamespace(attachment_is_empty=lambda p, kind: True)
    assistant.upload_via_chooser = lambda *args: False
    assistant.accept_consent_dialog = lambda *args: None
    assert assistant.upload_resume(page, document) is expected
    for item in page.locator('input[type=file]').all():
        assert item.evaluate('el => el.files.length') == (1 if item.get_attribute('id') == selected else 0)


@pytest.mark.parametrize("markup", ['<input type=file aria-label="Upload Video" accept="video/*">', '<input type=file aria-label="Upload Video">'])
def test_inventory_resume_path_also_rejects_video_slots(page, tmp_path, markup):
    import form_fields
    document = tmp_path / "resume.pdf"
    document.write_bytes(b"synthetic")
    page.set_content(markup)
    assert form_fields.attach_resume(page, document) is None
    assert page.locator('input').evaluate('e=>e.files.length') == 0
