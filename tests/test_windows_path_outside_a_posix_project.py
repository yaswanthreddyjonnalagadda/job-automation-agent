"""A Windows drive path is outside the project on a host that has no drives (f304).

CI runs on Linux, where pathlib reads "C:/elsewhere/secret.log" as a relative path, so it resolved inside the project and
got a reference. The dashboard's rule -- a file outside the project has no reference -- must hold on any host.
"""
from pathlib import Path

import web_ui


def test_a_windows_drive_path_has_no_reference_in_a_project_without_drives(monkeypatch):
    monkeypatch.setattr(web_ui, "BASE_DIR", Path("/srv/job-agent"))
    assert web_ui._artifact_ref("C:/elsewhere/secret.log") == ""
    assert web_ui._artifact_ref(r"D:\elsewhere\secret.log") == ""


def test_a_project_relative_path_still_has_its_reference(monkeypatch):
    monkeypatch.setattr(web_ui, "BASE_DIR", Path("/srv/job-agent"))
    assert web_ui._artifact_ref(Path("/srv/job-agent/logs/a.log")) == "logs/a.log"
