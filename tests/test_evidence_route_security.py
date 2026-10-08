import pytest

import diagnostics as d
import web_ui


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web_ui, 'BASE_DIR', tmp_path)
    (tmp_path/'output'/'job').mkdir(parents=True)
    (tmp_path/'logs').mkdir()
    with web_ui.app.test_client() as client:
        yield client


def test_safe_html_is_nonactive_and_legacy_is_rejected(client, tmp_path):
    path = tmp_path/'output'/'job'/'page.html'
    d.write_safe_text(path, d.sanitize_dom('<label>Email</label><script>alert(1)</script><img onerror="alert(1)">'))
    response = client.get('/evidence',query_string={'path':str(path)})
    assert response.status_code == 200
    assert response.mimetype == 'text/plain'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert 'script-src' in response.headers['Content-Security-Policy']
    assert b'<script' not in response.data and b'onerror' not in response.data
    legacy = path.with_name('legacy.html')
    legacy.write_text('<script>alert(1)</script>')
    assert client.get('/evidence',query_string={'path':str(legacy)}).status_code == 404
    # The manifest binds actual bytes: replacing safe content invalidates it.
    path.write_text('<script>alert(1)</script>')
    assert client.get('/evidence',query_string={'path':str(path)}).status_code == 404


@pytest.mark.parametrize('route', ['/evidence','/log'])
def test_paths_outside_approved_root_are_rejected(client, tmp_path, route):
    (tmp_path/'.env').write_text('SecretPassword!123')
    for path in ('../.env',str(tmp_path/'.env'),str(tmp_path/'other'/'page.html')):
        assert client.get(route,query_string={'path':path}).status_code == 404
    root = tmp_path / ('output' if route == '/evidence' else 'logs')
    wrong = root/'unsupported.exe'
    wrong.write_text('482193')
    assert client.get(route,query_string={'path':str(wrong)}).status_code == 404


@pytest.mark.parametrize('route', ['/evidence','/log'])
def test_symlink_escape_rejected(client, tmp_path, route):
    secret = tmp_path/'.env'
    secret.write_text('SecretPassword!123')
    root = tmp_path / ('output' if route == '/evidence' else 'logs')
    link = root/'escape.txt'
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip('symlink creation unavailable')
    assert client.get(route,query_string={'path':str(link)}).status_code == 404


def test_legacy_logs_are_not_exposed(client, tmp_path):
    path = tmp_path/'logs'/'ui_run_old.log'
    path.write_text('Privacy Test Person password=SecretPassword!123')
    assert client.get('/log',query_string={'path':str(path)}).status_code == 404


@pytest.mark.parametrize('route', ['/evidence','/log'])
def test_directory_link_or_junction_escape_rejected(client,tmp_path,route):
    import os
    import subprocess
    outside = tmp_path/'outside'
    outside.mkdir()
    d.write_safe_text(outside/'page.html', '<label>Email</label>')
    root = tmp_path / ('output' if route == '/evidence' else 'logs')
    link = root/'escape'
    if os.name == 'nt':
        subprocess.run(['cmd.exe','/c','mklink','/J',str(link),str(outside)],check=True,capture_output=True)
    else:
        link.symlink_to(outside,target_is_directory=True)
    assert client.get(route,query_string={'path':str(link/'page.html')}).status_code == 404
