"""Storage-only privacy policy for diagnostics. Never changes runtime authority.

Untrusted free text is omitted from structural exports. Pattern redaction is a
second layer, not evidence that an arbitrary applicant answer is safe to save.
"""
from __future__ import annotations

import ast
import hashlib
from functools import lru_cache
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup, Comment, NavigableString

CAPTURE_VERSION = 5
DOM_LIMIT = 2 * 1024 * 1024
TEXT_LIMIT = 1024 * 1024
CONSOLE_LIMIT = 512 * 1024
MANIFEST_LIMIT = 64 * 1024
PNG_LIMIT = 16 * 1024 * 1024
SAFE_EXTENSIONS = {'.html', '.txt', '.json', '.png', '.log', '.jsonl'}
PLACEHOLDER = '<redacted-value>'

# Exact labels only; no keyword test that might bless an applicant's answer.
SAFE_LABELS = frozenset('application|personal information|contact information|first name|middle name|last name|full name|email|phone|address|city|state|postal code|country|password|verification code|resume|cover letter|education|work experience|experience|review|continue|next|back|save|submit|submit application|sign in|create account|required|optional'.split('|'))
SAFE_ROLES = frozenset('button textbox checkbox radio combobox listbox option heading link group form dialog alert navigation main document generic text paragraph list listitem spinbutton iframe separator status progressbar banner'.split())
SAFE_TAGS = frozenset('html head body main section article div span p h1 h2 h3 h4 h5 h6 label legend fieldset form input textarea select option button a ul ol li table thead tbody tr th td nav header footer dialog br hr'.split())


@dataclass(frozen=True)
class PrivacyContext:
    values: tuple[str, ...] = ()


def sanitize_url(value: str) -> str:
    try:
        u = urlsplit(value)
        if u.scheme not in {'http', 'https'} or not u.hostname:
            return '<redacted-url>'
        # Unknown path segments can themselves be magic-link/session tokens.
        safe_segments = {'apply','application','applications','job','jobs','careers','career','verify','verification','login','signin','sign-in','reset','password','magic','session','auth','oauth','callback'}
        path = '/'.join(segment if segment.lower() in safe_segments or not segment else '<redacted>' for segment in u.path.split('/'))
        host = u.hostname
        if u.port:
            host += f':{u.port}'
        return urlunsplit((u.scheme, host, path, '<redacted>' if u.query else '', '<redacted>' if u.fragment else ''))
    except (ValueError, TypeError):
        return '<redacted-url>'


def sanitize_text(value: str, context: PrivacyContext | None = None) -> str:
    """Pattern/context redaction for copies; free-text exports also use projection."""
    text = value if isinstance(value, str) else PLACEHOLDER
    for held in sorted((context.values if context else ()), key=len, reverse=True):
        if held:
            text = re.sub(re.escape(held), PLACEHOLDER, text, flags=re.I)
    text = re.sub(r'https?://[^\s<>"\']+', lambda m: sanitize_url(m[0]), text)
    text = re.sub(r'(?i)\b(?:bearer\s+\S+|(?:password|otp|totp|verification[ _-]?code|passcode|api[ _-]?key|token|secret|cookie|csrf|session[ _-]?id)\s*[=: ]\s*[^\n,;]+)', '<redacted-secret>', text)
    text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]+|eyJ[A-Za-z0-9_.-]+)\b', '<redacted-secret>', text)
    text = re.sub(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '<redacted-email>', text)
    text = re.sub(r'(?<!\w)\+?\d[\d ()_.-]{7,}\d(?!\w)', PLACEHOLDER, text)
    text = re.sub(r'(?<!\w)\d{4,8}(?!\w)', '<redacted-secret>', text)
    text = re.sub(r'(?i)\b\d{1,6}\s+(?:[A-Za-z]+\s+){1,6}(?:street|st|avenue|ave|road|rd|lane|ln|drive|dr|boulevard|blvd)\b[^\n,;]*', PLACEHOLDER, text)
    text = re.sub(r'(?i)\b[^\s<>"\']+\.(?:pdf|docx?|rtf)\b', '<redacted-document>', text)
    return text


def safe_label(text) -> str:
    if not isinstance(text, str):
        return PLACEHOLDER
    normal = ' '.join(text.split()).strip(' *:').lower()
    return text.strip() if normal in SAFE_LABELS else PLACEHOLDER


def sanitize_dom(raw: str, context: PrivacyContext | None = None) -> str:
    soup = BeautifulSoup(raw, 'html.parser')
    for node in list(soup.find_all('script style template noscript meta link iframe object embed svg canvas img video audio'.split())):
        node.decompose()
    for node in list(soup.find_all(True)):
        if node.parent is None:
            continue  # A cleared editable/select region already removed this child.
        if node.name not in SAFE_TAGS:
            node.unwrap()
            continue
        if node.has_attr('contenteditable') or node.name in {'textarea','select'}:
            node.clear()
            node.string = PLACEHOLDER
        attrs = {}
        role = node.get('role')
        if role in SAFE_ROLES:
            attrs['role'] = role
        for key in ('required','disabled','checked','multiple'):
            if node.has_attr(key):
                attrs[key] = ''
        for key in ('aria-required','aria-disabled','aria-checked','aria-expanded','aria-invalid'):
            if node.get(key) in ('true','false','mixed'):
                attrs[key] = node[key]
        if node.has_attr('aria-label'):
            attrs['aria-label'] = safe_label(node['aria-label'])
        if node.name == 'input' and node.get('type') in {'text','email','password','tel','number','date','hidden','checkbox','radio','file','submit','button'}:
            attrs['type'] = node['type']
        node.attrs = attrs
    for text in list(soup.find_all(string=True)):
        if isinstance(text, Comment):
            text.extract()
        elif text.strip():
            text.replace_with(NavigableString(safe_label(sanitize_text(str(text), context))))
    return str(soup)


def sanitize_snapshot(text: str, context: PrivacyContext | None = None) -> str:
    """Preserve roles/known labels and boolean state; omit free text and values."""
    out = []
    for line in (text or '').splitlines()[:20000]:
        match = re.match(r'^(\s*)-\s+([a-zA-Z]+)(?:\s+"([^"]*)")?', line)
        if not match or match[2].lower() not in SAFE_ROLES:
            out.append(PLACEHOLDER)
            continue
        label = ' "' + safe_label(sanitize_text(match[3], context)) + '"' if match[3] is not None else ''
        states = ' '.join(re.findall(r'\[(?:required|disabled|checked|expanded)(?:=(?:true|false|mixed))?\]', line))
        out.append(f'{match[1]}- {match[2]}{label}' + (f' {states}' if states else ''))
    return '\n'.join(out)


def sanitize_console_logs(entries) -> list[dict]:
    result = []
    if type(entries) is not list:
        return result
    for entry in entries[-200:]:
        if type(entry) is not dict:
            continue
        kind = entry.get('type') if entry.get('type') in {'log','info','warning','warn','error','debug'} else 'log'
        # Preserve error category, never browser-controlled message bodies/objects.
        text = entry.get('text')
        category = re.search(r'\b(?:TypeError|ReferenceError|SyntaxError|RangeError|NetworkError)\b', text[:4096]) if isinstance(text,str) else None
        result.append({'type':kind, 'text':('Uncaught '+category[0]) if category else PLACEHOLDER})
    return result


EVENT_ENUMS = {
    'action': {'upload_resume','upload_cover_letter','fill_entries','press_next','fill','upload','navigate'},
    'target_kind': {'experience','education','field','document','button'},
    'evidence_kind': {'attachment_filename','entry_count','validation_text','field_value','page_transition'},
    'result': {'attempted','verified','unknown','failed','blocked'},
    'stage': {'review','application','authentication','submission'},
    'reason_code': {'capture_failed','validation_failed','verification_unavailable','owner_review'},
    'category': {'CAPTCHA','SMS_MFA','AUTHENTICATOR_MFA','SECURITY_KEY','PUSH_APPROVAL','ACCOUNT_LOCKED','ACCOUNT_CREATION_UNCERTAIN','FIELD_REQUIRED','UNSUPPORTED_CONTROL','VALIDATION_BLOCKER','ACTION_OUTCOME_UNKNOWN','APPLICATION_RECOVERY','OWNER_REVIEW','OTHER'},
    'outcome_kind': {'review','blocked','stuck','captcha','submitted','user','error',
                     'needs_user','owner_needed','gave_up','disqualified_policy_mismatch',
                     'blocked_validation_loop','no_sponsorship'},
    'portal': {'workday','greenhouse','lever','ashby','successfactors','icims','unknown',
               'amazon','eightfold','generic'},
}


def sanitize_event_payload(payload) -> dict | None:
    if payload is None:
        return None
    if type(payload) is not dict:
        return {}
    safe = {}
    for key,value in payload.items():
        if key == 'portal' and type(value) is str:
            try:
                from sites import adapter_for
                name = value if value in EVENT_ENUMS['portal'] else adapter_for(value).name
                safe[key] = name if name in EVENT_ENUMS['portal'] else 'unknown'
            except Exception:
                safe[key] = 'unknown'
        elif key in EVENT_ENUMS and type(value) is str and value in EVENT_ENUMS[key]:
            safe[key] = value
        elif key in {'count','error_count','step','unapproved_field_count','mismatched_field_count'} and type(value) is int and 0 <= value <= 100000:
            safe[key] = value
        elif key in {'eligible','verified','attempted'} and type(value) is bool:
            safe[key] = value
    comparisons = payload.get('field_comparisons')
    if type(comparisons) is list:
        safe['unapproved_field_count'] = sum(1 for item in comparisons[:100000]
                                             if type(item) is dict and not item.get('approved'))
        safe['mismatched_field_count'] = sum(1 for item in comparisons[:100000]
                                             if type(item) is dict and item.get('matches') is False)
    if safe.get('eligible') is False:
        safe['reason_code'] = 'owner_review'
    return safe


JSON_KEYS = frozenset('screenshot required_still_blank errors_shown fields_filled fields_matched_but_still_blank screening_answers field_comparisons label required disabled checked matches approved on_form value eligible reasons count error_count captcha resume_attached schema_version sanitization_status truncated aria_snapshot role children name timestamp reason url domain warnings_shown unanswered_questions attestations_pending ambiguous_choices unsupported_questions identity_checks attached_documents'.split())


def sanitize_json(value, key='', depth=0):
    if key in {'value', 'approved', 'on_form'}:
        return PLACEHOLDER
    if depth > 12:
        return PLACEHOLDER
    if type(value) is dict:
        return {k:sanitize_json(v,k,depth+1) for k,v in list(value.items())[:2000] if k in JSON_KEYS}
    if type(value) is list:
        return [sanitize_json(v,key,depth+1) for v in value[:2000]]
    if value is None or type(value) is bool:
        return value
    if type(value) in {int,float}:
        return value if key in {'count','error_count','schema_version'} else PLACEHOLDER
    if type(value) is str:
        if key in {'label','name'}:
            return safe_label(value)
        if key == 'aria_snapshot':
            return sanitize_snapshot(value)
        if key == 'role':
            return value if value.lower() in SAFE_ROLES else PLACEHOLDER
        if key == 'url':
            return sanitize_url(value)
    return PLACEHOLDER


def _bounded(text, limit):
    raw = text.encode('utf-8')
    return raw[:limit].decode('utf-8',errors='ignore'), len(raw) > limit


def _regular_path(path: Path) -> bool:
    return not any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
                   for p in (path,*path.parents))


def _safe_artifact_name(name):
    if name in DIAGNOSTIC_FILES or DIAGNOSTIC_PATTERNS.fullmatch(name):
        return True
    match = re.fullmatch(r'[0-9_]+_([A-Za-z_]+)\.(?:txt|png)', name)
    if not match:
        return False
    import account_state
    states = {account_state.SIGNED_IN, account_state.LOCKED, account_state.WRONG_PASSWORD,
              account_state.ACCOUNT_EXISTS, account_state.CODE_ENTRY, account_state.MFA_REQUIRED,
              account_state.VERIFY_EMAIL, account_state.CREATE_FORM, account_state.SIGN_IN_FORM,
              account_state.EMAIL_FIRST, account_state.CHOOSER, account_state.LOADING, account_state.NONE}
    return match[1] in states or match[1] == 'ACCOUNT'


def _manifest(path: Path, truncated=False):
    target = path.parent/'manifest.json'
    data = {'schema_version':1, 'capture_version':CAPTURE_VERSION,
            'created_at':datetime.now(timezone.utc).isoformat(),
            'reason_code':'diagnostic_capture', 'application_hash':hashlib.sha256(str(path.parent).encode()).hexdigest()[:16],
            'sanitization_status':'safe', 'artifacts':{}}
    if target.is_file() and _regular_path(target) and target.stat().st_size <= MANIFEST_LIMIT:
        try:
            previous = json.loads(target.read_text(encoding='utf-8'))
            if previous.get('capture_version') == CAPTURE_VERSION:
                # Rebuild only valid fixed-format entries; no inherited raw metadata.
                for name,entry in previous.get('artifacts',{}).items():
                    if _safe_artifact_name(name) and Path(name).name == name and type(entry) is dict and re.fullmatch(r'[0-9a-f]{64}',str(entry.get('sha256',''))):
                        data['artifacts'][name] = {'sha256':entry['sha256'],'truncated':entry.get('truncated') is True}
        except (ValueError,TypeError,AttributeError):
            pass
    if len(data['artifacts']) >= 500 and path.name not in data['artifacts']:
        raise ValueError('manifest capacity')
    data['artifacts'][path.name] = {'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'truncated':truncated}
    encoded = json.dumps(data,sort_keys=True)
    if len(encoded.encode()) > MANIFEST_LIMIT:
        raise ValueError('manifest capacity')
    target.write_text(encoded,encoding='utf-8')


def write_safe_text(path: Path, sanitized: str, limit=TEXT_LIMIT) -> bool:
    """Caller must supply a structural sanitized projection, never raw page text."""
    path = Path(path)
    try:
        if path.suffix not in SAFE_EXTENSIONS or not _regular_path(path) or not _safe_artifact_name(path.name):
            return False
        path.parent.mkdir(parents=True,exist_ok=True)
        text,truncated = _bounded(sanitized,limit)
        path.write_text(text,encoding='utf-8')
        _manifest(path,truncated)
        return True
    except Exception:
        return False


def write_safe_json(path: Path, value, limit=TEXT_LIMIT) -> bool:
    try:
        safe = sanitize_json(value)
        encoded = json.dumps(safe,ensure_ascii=True)
        if len(encoded.encode()) > limit:
            encoded = json.dumps({'truncated':True,'reason':'size_limit'})
        success = write_safe_text(path,encoded,limit)
        if success and type(safe) in {dict,list} and len(json.dumps(safe,ensure_ascii=True).encode()) > limit:
            _manifest(Path(path),truncated=True)
        return success
    except Exception:
        return False


def read_safe_artifact(path: Path) -> bytes | None:
    """Verify the same bounded bytes the viewer will serve, not a later reread."""
    path = Path(path)
    try:
        manifest = path.parent/'manifest.json'
        if not _safe_artifact_name(path.name) or not _regular_path(path) or not _regular_path(manifest) or path.suffix not in SAFE_EXTENSIONS or not path.is_file():
            return None
        limit = PNG_LIMIT if path.suffix == '.png' else DOM_LIMIT
        if path.stat().st_size > limit or manifest.stat().st_size > MANIFEST_LIMIT:
            return None
        data = json.loads(manifest.read_text(encoding='utf-8'))
        with path.open('rb') as stream:
            content = stream.read(limit + 1)
        if len(content) > limit:
            return None
        if data.get('capture_version') == CAPTURE_VERSION and data.get('sanitization_status') == 'safe' and data['artifacts'][path.name]['sha256'] == hashlib.sha256(content).hexdigest():
            return content
        return None
    except (OSError,ValueError,KeyError,TypeError):
        return None


def is_safe_artifact(path: Path) -> bool:
    return read_safe_artifact(path) is not None


def capture_safe_dom(page, path: Path, context=None) -> bool:
    try:
        return write_safe_text(path,sanitize_dom(page.content(),context),DOM_LIMIT)
    except Exception:
        return False


MASK_CSS = """
* { color: transparent !important; -webkit-text-fill-color: transparent !important;
text-shadow: none !important; background-image: none !important; caret-color: transparent !important; }
*::before, *::after { content: none !important; }
input, textarea, select, [contenteditable], img, picture, svg, canvas, video,
audio, iframe, object, embed { visibility: hidden !important; }
"""


def capture_safe_screenshot(page, path: Path) -> bool:
    path = Path(path)
    token = uuid.uuid4().hex
    installed = False
    success = False
    try:
        if not _regular_path(path) or not _safe_artifact_name(path.name):
            return False
        # Top-level masking hides frame hosts and custom elements (including closed
        # shadow roots). No values, input events, navigation or control clicks.
        installed = page.evaluate("""({token, css}) => {
            const style=document.createElement('style');
            style.dataset.diagnosticMask=token;
            const custom=[...new Set([...document.querySelectorAll('*')].map(e=>e.localName).filter(n=>n.includes('-')))];
            style.textContent=css + (custom.length ? custom.join(',')+' {visibility:hidden !important;}' : '');
            document.documentElement.appendChild(style);
            const probe=document.createElement('span');
            probe.textContent='mask check'; document.documentElement.appendChild(probe);
            const ok=getComputedStyle(probe).color==='rgba(0, 0, 0, 0)'; probe.remove();
            const width=Math.max(document.documentElement.scrollWidth,document.body?.scrollWidth||0,innerWidth);
            const height=Math.max(document.documentElement.scrollHeight,document.body?.scrollHeight||0,innerHeight);
            const cover=document.createElement('div'); cover.dataset.diagnosticMask=token;
            for(const [k,v] of Object.entries({all:'initial',display:'block',position:'absolute',left:'0px',top:'0px',
                width:width+'px',height:height+'px',margin:'0',padding:'0',border:'0',transform:'none',
                opacity:'1',visibility:'visible',background:'#202124','pointer-events':'none','z-index':'2147483647'}))
                cover.style.setProperty(k,v,'important');
            document.documentElement.appendChild(cover);
            const r=cover.getBoundingClientRect();
            return ok && Math.abs(r.left+scrollX)<1 && Math.abs(r.top+scrollY)<1 && r.width>=width && r.height>=height;
        }""", {'token':token,'css':MASK_CSS+'\n*:not(:defined) { visibility:hidden !important; }'}) is True
        if not installed:
            return False
        path.parent.mkdir(parents=True,exist_ok=True)
        # Native full-document mask is the final conservative boundary: arbitrary
        # CSS, closed shadow roots, canvas, frames and document previews cannot
        # reveal text/pixels. Structural DOM carries the useful debugging detail.
        # Screenshot's transient stylesheet also pierces open shadow roots/frames.
        data = page.screenshot(full_page=True,style=MASK_CSS,mask=[page.locator(f'div[data-diagnostic-mask="{token}"]')],mask_color='#202124',timeout=15000)
        still_masked = page.evaluate('''token => {
            const cover=document.querySelector(`div[data-diagnostic-mask="${token}"]`);
            return !!cover && getComputedStyle(cover).visibility==='visible' && getComputedStyle(cover).opacity==='1';
        }''', token) is True
        if not still_masked or type(data) is not bytes or len(data) > PNG_LIMIT:
            return False
        path.write_bytes(data)
        _manifest(path)
        success = True
    except Exception:
        pass
    finally:
        try:
            page.evaluate("""token => {
                for(const s of document.querySelectorAll('[data-diagnostic-mask]'))
                    if(s.dataset.diagnosticMask===token) s.remove();
            }""", token)
        except Exception:
            success = False
    return success


def capture_bundle(page, folder: Path, *, reason='', console_logs=None, screenshot_name='page.png', html_name='page.html') -> dict:
    folder = Path(folder)
    paths = {}
    if capture_safe_screenshot(page,folder/screenshot_name):
        paths['screenshot'] = str(folder/screenshot_name)
    if capture_safe_dom(page,folder/html_name):
        paths['html'] = str(folder/html_name)
    try:
        snapshot = page.locator('body').aria_snapshot(mode='ai',timeout=5000)
        write_safe_json(folder/'axtree_dump.json',{'aria_snapshot':snapshot})
    except Exception:
        pass
    logs = sanitize_console_logs(console_logs if console_logs is not None else getattr(page,'_console_logs',[]))
    write_safe_text(folder/'console_logs.json',json.dumps(logs),CONSOLE_LIMIT)
    write_safe_json(folder/'failure_meta.json',{'reason':PLACEHOLDER,'url':getattr(page,'url','')})
    if 'screenshot' not in paths or 'html' not in paths:
        logging.getLogger(__name__).warning('DIAGNOSTIC_CAPTURE: some artifacts omitted')
    return paths


def retention_days(value=None) -> int:
    if value is None:
        value = os.getenv('DIAGNOSTIC_RETENTION_DAYS','7')
    try:
        if type(value) not in {str,int} or (isinstance(value,str) and not value.strip().isdigit()):
            return 7
        days = int(value)
        return days if 0 <= days <= 30 else 7
    except (ValueError,TypeError):
        return 7


DIAGNOSTIC_FILES = {'page.png','page.html','screenshot.png','page_state.html','axtree_dump.json','console_logs.json','failure_meta.json','manifest.json','review_screenshot.png','review_page.html','review_summary.json','comparison.json','validation.json','submitted_confirmation.png'}
DIAGNOSTIC_PATTERNS = re.compile(r'^(?:stopped_.*\.(?:png|txt)|dropdown_dump_.*\.txt|forensic_disqualified_policy_mismatch_.*\.png|page_\d+\.txt|(?:ui_run|run)_.*\.(?:log|jsonl))$')


def cleanup_expired_diagnostics(base_dir: Path, days=None, active_dir=None, now=None) -> int:
    """Bounded, no symlink traversal; never remove a job/material directory."""
    base_dir = Path(base_dir)
    cutoff = (time.time() if now is None else now) - retention_days(days)*86400
    active = Path(active_dir).absolute() if active_dir else None
    removed = 0
    budget = 10000
    # Only fixed known roots; bounded depth, no whole-repo walk.
    stack = [(base_dir/root,0) for root in ('output','runs','logs')]
    while stack and budget > 0:
        folder,depth = stack.pop()
        if not _regular_path(folder) or not folder.is_dir():
            continue
        try:
            for path in folder.iterdir():
                budget -= 1
                if budget < 0:
                    break
                if not _regular_path(path) or (active and (path.absolute()==active or active in path.absolute().parents)):
                    continue
                if path.is_dir():
                    # Restrict subtrees to known locations; job folders at output depth 0.
                    # `pages/` is deliberately NEVER entered: it holds `page_NN.txt`, a name
                    # shared by two unrelated things -- the new, run-scoped, genuinely
                    # ephemeral snapshots PageAgent._save() writes under a timestamped
                    # subfolder (already retained/pruned by page_agent.keep_latest_runs(),
                    # a separate, existing, count-based mechanism), and the OLD-style, FLAT,
                    # PERMANENT real-application-page recordings this project keeps for
                    # replay testing. A filename-only match cannot tell them apart, so this
                    # function must never delete anything under `pages/` at all (confirmed,
                    # irreversible loss found in an earlier version of this function, which
                    # entered `pages/` and deleted the flat recordings once they aged past
                    # the retention cutoff -- `output/` is gitignored, so there was no git
                    # recovery). `account/` is unaffected: it holds only this run's own
                    # account-state diagnostic screenshots, never permanent recordings.
                    root = folder.relative_to(base_dir).parts[0]
                    allowed = (root=='output' and depth==0) or (root=='output' and depth==1 and (path.name.startswith(('evidence_','step_')) or path.name=='account')) or (root=='runs' and depth==0) or (root=='logs' and path.name=='account_failures' and depth==0)
                    if allowed:
                        stack.append((path,depth+1))
                elif path.is_file() and _safe_artifact_name(path.name) and path.stat().st_mtime <= cutoff:
                    path.unlink()
                    removed += 1
            if folder.name.startswith(('evidence_','step_')) or folder.parent.name=='runs':
                try:
                    folder.rmdir()  # Only empty folders; never recursively delete materials.
                except OSError:
                    pass
        except OSError:
            pass
    return removed


@lru_cache(maxsize=256)
def _static_log_templates(filename):
    try:
        tree = ast.parse(Path(filename).read_text(encoding='utf-8'))
        return frozenset(node.args[0].value for node in ast.walk(tree)
                         if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
                         and node.func.attr in {'debug','info','warning','error','exception','critical','log'}
                         and node.args and isinstance(node.args[0],ast.Constant)
                         and isinstance(node.args[0].value,str))
    except Exception:
        return frozenset()


def install_log_privacy():
    """Redact before ANY handler, including handlers installed after startup.

    Non-numeric runtime interpolation is untrusted. Keep the static operation
    template and counts; omit live values and exception bodies/tracebacks.
    Existing safety redaction can still run as an additional layer.
    """
    previous = logging.getLogRecordFactory()
    if getattr(previous,'_diagnostic_privacy',False):
        return
    def factory(*args,**kwargs):
        record = previous(*args,**kwargs)
        try:
            original = record.msg
            if record.args and isinstance(original,str) and original in _static_log_templates(record.pathname):
                def safe_arg(value):
                    if type(value) in {int,float,bool}:
                        return value
                    return PLACEHOLDER
                held = {k:safe_arg(v) for k,v in record.args.items()} if isinstance(record.args,dict) else tuple(safe_arg(v) for v in record.args)
                try:
                    message = original % held
                except (TypeError,ValueError):
                    message = 'runtime operation (private details omitted)'
            else:
                # F-strings/objects have no independently trusted static template.
                message = original if isinstance(original,str) and original in _static_log_templates(record.pathname) else 'runtime operation (private details omitted)'
            record.msg = sanitize_text(message)
            record.args = ()
            if record.exc_info:
                record.msg += ' [exception omitted]'
                record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        except Exception:
            record.msg = 'runtime operation (private details omitted)'
            record.args = ()
            record.exc_info = record.exc_text = record.stack_info = None
        return record
    factory._diagnostic_privacy = True
    logging.setLogRecordFactory(factory)


LOG_OPERATIONS = frozenset("FIELD_ACTION FIELD_PRESERVED ANSWER_LIBRARY FIELD_COMMITTED ATTACHED UPLOAD ACCOUNT_STATE LOGIN CODE PASSCODE PASSCODE_OK VERIFY_LINK RECOVERY RECOVERY_OK ACCOUNT_CREATE_FAILED ACCOUNT_CREATE_ATTEMPT ACCOUNT_CREATED REVIEW_READY READY_TO_SUBMIT SUBMITTED_BY_USER SUBMITTED SKIPPED READ PAGE_FULLY_KNOWN UNHANDLED_ERROR VALIDATION_FAILED HANDOFF STARTED FINISHED".split())


def sanitize_log_line(line: str) -> str:
    # stdout/stderr can bypass logging entirely; retain only enumerated operations.
    match = re.search(r'\b([A-Z][A-Z_]+):', line[:4096])
    operation = match[1] if match and match[1] in LOG_OPERATIONS else 'RUNTIME'
    return operation + ': private details omitted\n'


def append_log_record(path: Path, record: logging.LogRecord) -> bool:
    """Do not copy unmarked legacy logs or trust manually constructed records."""
    try:
        message = sanitize_log_line(record.getMessage()).strip()
        entry = {
            'time': datetime.now(timezone.utc).isoformat(),
            'level': record.levelname if record.levelname in {'DEBUG','INFO','WARNING','ERROR','CRITICAL'} else 'INFO',
            'logger': 'runtime',
            'event': message.split(':', 1)[0],
            'message': message,
        }
        previous = read_safe_artifact(path)
        text = (previous.decode('utf-8') if previous is not None else '') + json.dumps(entry) + '\n'
        if len(text.encode()) > TEXT_LIMIT:
            text = text[-TEXT_LIMIT:].split('\n', 1)[-1]
        return write_safe_text(path, text)
    except Exception:
        return False


class PrivateRunLog:
    """Bounded sanitized stdout export, manifest updated after each batch."""
    def __init__(self, path, source_id=""):
        self.path = Path(path)
        self.text = 'STARTED: diagnostic privacy version 5\n'
        if re.fullmatch(r'[0-9a-f]{16}', source_id):
            self.text += 'SOURCE_ID: ' + source_id + '\n'
        write_safe_text(self.path,self.text)

    def append(self, line):
        self.text += sanitize_log_line(line)
        if len(self.text.encode()) > TEXT_LIMIT:
            self.text = self.text[-TEXT_LIMIT//2:]
        write_safe_text(self.path,self.text)

EVENT_KINDS = frozenset("note status status_change error evidence auto_submit resume_attached action_outcome_unknown action_validation_failed handoff_started handoff_required other".split())


def sanitize_event(event):
    safe = dict(event)
    kind = safe.get('kind')
    safe['kind'] = kind if kind in EVENT_KINDS else 'other'
    safe['message'] = 'event: ' + safe['kind']
    safe['payload'] = sanitize_event_payload(safe.get('payload'))
    for key in ('screenshot_path','html_path'):
        value=safe.get(key)
        safe[key] = value if isinstance(value,str) and is_safe_artifact(Path(value)) else None
    return safe
