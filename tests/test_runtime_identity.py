import io
import json
from pathlib import Path
import urllib.error
import pytest
import launch_dashboard
import visible_desktop

@pytest.fixture
def checkout(tmp_path, monkeypatch):
    for name in ('web_ui.py','apply.py','apply_flow.py','page_agent.py','interaction.py'):
        (tmp_path/name).write_text('# fixture')
    monkeypatch.setattr(launch_dashboard, 'ROOT', tmp_path)
    monkeypatch.setattr(visible_desktop, 'refuse_if_invisible', lambda: None)
    monkeypatch.setattr(launch_dashboard.os, 'chdir', lambda path: None)
    return tmp_path

def test_launcher_reuses_only_the_same_loaded_runtime(checkout, monkeypatch):
    payload={'directory':str(checkout),'source_id':launch_dashboard.source_id(checkout)}
    monkeypatch.setattr(launch_dashboard.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(json.dumps(payload).encode()))
    launch_dashboard.main()

@pytest.mark.parametrize('change',['directory','source'])
def test_launcher_refuses_a_different_or_stale_runtime(checkout, monkeypatch, change):
    payload={'directory':str(checkout),'source_id':launch_dashboard.source_id(checkout)}
    if change=='directory': payload['directory']=str(checkout/'other')
    else: (checkout/'page_agent.py').write_text('# new field reading')
    monkeypatch.setattr(launch_dashboard.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(json.dumps(payload).encode()))
    with pytest.raises(RuntimeError,match='different dashboard runtime'):
        launch_dashboard.main()

def test_launcher_refuses_an_unversioned_old_dashboard(checkout, monkeypatch):
    def old_server(*args,**kwargs):
        raise urllib.error.HTTPError('http://127.0.0.1:5000/runtime',404,'Not Found',{},None)
    monkeypatch.setattr(launch_dashboard.urllib.request,'urlopen',old_server)
    with pytest.raises(RuntimeError,match='unversioned dashboard'):
        launch_dashboard.main()
