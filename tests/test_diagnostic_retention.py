import os
import time

import pytest

import diagnostics as d


@pytest.mark.parametrize('value,expected', [('0',0),('30',30),('7',7),('-1',7),('31',7),('bad',7),('',7)])
def test_retention_config(value, expected):
    assert d.retention_days(value) == expected


def test_cleanup_removes_only_old_diagnostics(tmp_path):
    output = tmp_path / 'output'
    job = output / 'job'
    old = job / 'evidence_old'
    old.mkdir(parents=True)
    new = job / 'evidence_new'
    new.mkdir()
    (old / 'page.html').write_text('old')
    (new / 'page.html').write_text('new')
    protected = [job / 'resume.pdf', job / 'cover_letter.txt', job / 'checkpoint.json',
                 tmp_path / 'data' / 'applications.db', job / 'unrelated.txt',
                 job / 'account' / 'resume.txt', job / 'account' / 'cover_letter.txt']
    account_diagnostic = job / 'account' / '20261001_120000_sign_in_form.txt'
    account_diagnostic.parent.mkdir()
    account_diagnostic.write_text('old diagnostic')
    for p in protected:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('protected')
    timestamp = time.time() - 10 * 86400
    os.utime(old / 'page.html', (timestamp,timestamp))
    os.utime(old,(timestamp,timestamp))
    os.utime(account_diagnostic,(timestamp,timestamp))
    for p in protected:
        os.utime(p,(timestamp,timestamp))
    d.cleanup_expired_diagnostics(tmp_path, days=7)
    assert not old.exists() and new.exists()
    assert not account_diagnostic.exists()
    assert all(p.read_text() == 'protected' for p in protected)


def test_zero_retention_preserves_active_capture(tmp_path):
    active = tmp_path / 'runs' / 'active'
    inactive = tmp_path / 'runs' / 'inactive'
    for p in (active,inactive):
        p.mkdir(parents=True)
        (p/'screenshot.png').write_bytes(b'old')
    d.cleanup_expired_diagnostics(tmp_path, days=0, active_dir=active)
    assert active.exists() and not inactive.exists()


def test_symlink_target_and_material_in_diagnostic_folder_are_preserved(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    protected = outside / 'resume.pdf'
    protected.write_text('protected')
    root = tmp_path / 'output' / 'job'
    root.mkdir(parents=True)
    try:
        (root/'evidence_link').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('symlink creation unavailable')
    folder = root/'evidence_old'
    folder.mkdir()
    (folder/'resume.pdf').write_text('material')
    (folder/'page.html').write_text('old')
    d.cleanup_expired_diagnostics(tmp_path, days=0)
    assert protected.read_text() == 'protected'
    assert (folder/'resume.pdf').read_text() == 'material'
    assert not (folder/'page.html').exists()


def test_directory_junction_or_link_is_never_followed(tmp_path):
    import subprocess
    outside = tmp_path/'outside'
    outside.mkdir()
    target = outside/'page.html'
    target.write_text('protected outside diagnostic-named file')
    root = tmp_path/'output'/'job'
    root.mkdir(parents=True)
    link = root/'evidence_link'
    if os.name == 'nt':
        subprocess.run(['cmd.exe','/c','mklink','/J',str(link),str(outside)],check=True,capture_output=True)
    else:
        link.symlink_to(outside,target_is_directory=True)
    d.cleanup_expired_diagnostics(tmp_path,days=0)
    assert target.read_text() == 'protected outside diagnostic-named file'
