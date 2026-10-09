"""Synthetic privacy regressions; no owner data is needed."""
import json

import pytest

import diagnostics as d

SENTINELS = (
    "Privacy Test Person", "privacy-test@example.invalid", "+1-202-555-0199",
    "123 Privacy Sentinel Street", "SecretPassword!123", "482193",
    "eyJprivacy.sentinel.token", "SENSITIVE_RESUME_CONTENT_SENTINEL",
    "SENSITIVE_APPLICATION_ANSWER_SENTINEL", "Privacy_Test_Person_Resume.pdf",
)


def assert_private(text):
    assert all(value not in text for value in SENTINELS)


def test_text_and_url_copies_hide_secrets():
    ctx = d.PrivacyContext(SENTINELS)
    assert_private(d.sanitize_text(" ".join(SENTINELS), ctx))
    url = "https://example.invalid/verify?token=abc#secret"
    assert d.sanitize_url(url) == "https://example.invalid/verify?<redacted>#<redacted>"
    assert url.endswith("?token=abc#secret")
    for text in ("Bearer eyJprivacy.sentinel.token", "otp 482193", "Cookie: session=482193"):
        assert_private(d.sanitize_text(text))


def test_dom_projects_structure_without_untrusted_text():
    raw = '<h1>Application</h1><label>Email</label><input required disabled value="SecretPassword!123">'
    raw += '<textarea>SENSITIVE_RESUME_CONTENT_SENTINEL</textarea>'
    raw += '<div contenteditable>123 Privacy Sentinel Street</div>'
    raw += '<input type=hidden value="482193"><script>{"token":"eyJprivacy.sentinel.token"}</script>'
    raw += '<img onerror="482193" src="javascript:482193"><iframe srcdoc="482193"></iframe>'
    raw += '<span>Privacy Test Person</span><meta content="482193">'
    safe = d.sanitize_dom(raw)
    assert_private(safe)
    assert '<script' not in safe and 'onerror' not in safe and 'srcdoc' not in safe
    assert 'javascript:' not in safe and 'value=' not in safe
    assert 'Email' in safe and 'required' in safe and 'disabled' in safe
    for rich in ('<div contenteditable><b>Privacy Test Person</b></div>',
                 '<select><option><strong>Privacy Test Person</strong></option></select>'):
        assert_private(d.sanitize_dom(rich))


def test_console_is_untrusted_and_bounded():
    entries = [{"type": "error", "text": " ".join(SENTINELS) * 1000,
                "location": {"url": "https://example.invalid/?token=secret"}}] * 300
    safe = d.sanitize_console_logs(entries)
    assert len(safe) == 200
    assert_private(json.dumps(safe))
    assert len(json.dumps(safe).encode()) <= d.CONSOLE_LIMIT
    assert all(len(e["text"].encode()) <= 2048 for e in safe)


def test_failed_sanitizer_never_writes_raw(tmp_path, monkeypatch):
    class Page:
        def content(self):
            return "SecretPassword!123"
    monkeypatch.setattr(d, "sanitize_dom", lambda *a, **k: (_ for _ in ()).throw(ValueError("SecretPassword!123")))
    assert not d.capture_safe_dom(Page(), tmp_path / "page.html")
    assert not (tmp_path / "page.html").exists()


def test_safe_json_masks_answers_and_bounds_sanitized_output(tmp_path):
    path = tmp_path / "review_summary.json"
    assert d.write_safe_json(path, {"fields_filled": [{"label": "Email", "value": SENTINELS[1]}],
                                    "errors_shown": [SENTINELS[3]], "count": 3})
    assert_private(path.read_text())
    assert d.is_safe_artifact(path)
    assert not d.is_safe_artifact(tmp_path / "unknown.json")
    for value in (True, False, 123):
        assert d.sanitize_json({'value':value}) == {'value':d.PLACEHOLDER}


def test_truncation_is_sanitized_marked_and_json_remains_valid(tmp_path):
    path = tmp_path/'page.html'
    assert d.write_safe_text(path,d.sanitize_dom('<p>'+SENTINELS[3]*100+'</p>'),limit=10)
    assert_private(path.read_text())
    manifest=json.loads((tmp_path/'manifest.json').read_text())
    assert manifest['artifacts']['page.html']['truncated'] is True
    path = tmp_path/'validation.json'
    assert d.write_safe_json(path,{'errors_shown':[SENTINELS[3]]*500},limit=100)
    assert json.loads(path.read_text())['truncated'] is True
    assert json.loads((tmp_path/'manifest.json').read_text())['artifacts']['validation.json']['truncated'] is True


def test_events_keep_b4_metadata_without_values():
    safe = d.sanitize_event_payload({"action": "upload_resume", "evidence_kind": "attachment_filename",
                                     "error_count": 2, "answer": SENTINELS[-2], "url": SENTINELS[6]})
    assert safe == {"action": "upload_resume", "evidence_kind": "attachment_filename", "error_count": 2}
    assert_private(json.dumps(safe))
    handoff = d.sanitize_event_payload({'portal':'tenant.myworkdayjobs.com',
                                        'outcome_kind':'owner_needed','category':'SMS_MFA'})
    assert handoff == {'portal':'workday','outcome_kind':'owner_needed','category':'SMS_MFA'}


def test_navigation_failures_keep_only_known_error_categories():
    assert d.sanitize_event_payload({'failure_kind':'hidden'}) == {'failure_kind':'hidden'}
    assert d.sanitize_event_payload({'failure_kind':SENTINELS[0], 'exception':SENTINELS[1]}) == {}


def test_sqlite_events_do_not_persist_private_messages_or_payloads(tmp_path):
    from job_tracker import JobTracker
    tracker = JobTracker(tmp_path / 'applications.db')
    tracker.create(dedup_key='synthetic', title='Engineer', company='Example', location='Remote', url='https://example.invalid/job')
    tracker.record_event('synthetic','action_outcome_unknown',' '.join(SENTINELS),
                         payload={'action':'upload_resume','evidence_kind':'attachment_filename','answer':SENTINELS[-2]})
    event = tracker.events('synthetic')[0]
    assert_private(json.dumps(event,default=str))
    assert event['payload']['action'] == 'upload_resume'
    assert event['payload']['evidence_kind'] == 'attachment_filename'


def test_postgres_event_boundary_uses_same_projection(monkeypatch):
    import db
    class Connection:
        statements = []
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,sql,params=None):
            self.statements.append((sql,params))
            return self
        def fetchone(self): return {'id':1}
    conn = Connection()
    tracker = db.PostgresTracker.__new__(db.PostgresTracker)
    monkeypatch.setattr(tracker,'_connect',lambda:conn)
    monkeypatch.setattr(db,'Json',lambda value:value)
    tracker.record_event('synthetic','action_outcome_unknown',' '.join(SENTINELS),
                         payload={'action':'upload_resume','evidence_kind':'attachment_filename','answer':SENTINELS[-2]})
    params = conn.statements[-1][1]
    assert_private(json.dumps(params))
    assert params[-1] == {'action':'upload_resume','evidence_kind':'attachment_filename'}


def test_log_record_redaction_covers_dynamic_values_and_tracebacks(caplog):
    import logging
    logger = logging.getLogger("page_agent")
    d.install_log_privacy()
    with caplog.at_level(logging.INFO):
        logger.info("KNEW: %s = %r", "Email", SENTINELS[1])
        logger.info("ATTACHED: %s", SENTINELS[-1])
        logger.info(SENTINELS[0])
        try:
            raise ValueError(" ".join(SENTINELS))
        except ValueError:
            logger.exception("Capture failed: %s", SENTINELS[3])
    assert_private(caplog.text)
    assert "KNEW" in caplog.text and "ATTACHED" in caplog.text


def test_stdout_is_sanitized_before_disk(tmp_path):
    log = d.PrivateRunLog(tmp_path/'ui_run_123.log')
    log.append(' '.join(SENTINELS))
    log.append('FIELD_COMMITTED: '+SENTINELS[-2])
    assert_private(log.path.read_text())
    assert 'FIELD_COMMITTED' in log.path.read_text()
    assert d.is_safe_artifact(log.path)


def test_json_log_handler_does_not_retrust_legacy_bytes_or_raw_records(tmp_path):
    import logging
    import apply_flow
    path = tmp_path/'run_123.jsonl'
    path.write_text(' '.join(SENTINELS))
    handler = apply_flow.JsonLogHandler(path)
    record = logging.LogRecord(SENTINELS[0], logging.INFO, __file__, 1,
                               'FIELD_COMMITTED: '+' '.join(SENTINELS), (), None)
    handler.emit(record)
    assert_private(path.read_text())
    assert json.loads(path.read_text())['event'] == 'FIELD_COMMITTED'
    assert d.is_safe_artifact(path)
