"""Exercise previously swallowed fallback failures with isolated dependencies."""
import ast
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import apply_flow
from browser_automation import JobApplicationAssistant


@pytest.mark.parametrize("already_selected", [False, True])
def test_searchable_answers_use_adapter_selection(monkeypatch, tmp_path, already_selected):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data/_dropdown_answers.json").write_text('{"searchable":{"source":["Referral"]}}')
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    selected = Mock(return_value="Referral" if already_selected else "")
    a.adapter = lambda page: SimpleNamespace(_multiselect_selection=selected)
    a.select_from_searchable_input = Mock()
    page = SimpleNamespace(locator=lambda *args: SimpleNamespace(first=SimpleNamespace(count=lambda: 1)))
    a.apply_dropdown_answers(page)
    selected.assert_called_once_with(page, "source")
    assert a.select_from_searchable_input.call_count == (0 if already_selected else 1)


def test_upload_tile_can_finish_in_dialog():
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    tile = SimpleNamespace(is_visible=lambda: True, inner_text=lambda: "Resume")
    tiles = SimpleNamespace(count=lambda: 1, nth=lambda i: tile)
    tiles.filter = lambda **kw: tiles
    a.adapter = lambda page: SimpleNamespace(upload_attachment=lambda *args: False)
    a._click_resiliently = Mock()
    a.upload_in_dialog = Mock(return_value=True)
    page = SimpleNamespace(
        get_by_text=lambda *args, **kw: tiles,
        locator=lambda *args, **kw: tiles,
        expect_file_chooser=Mock(side_effect=TimeoutError("dialog instead")),
        wait_for_timeout=lambda *args: None,
        get_by_role=lambda *args, **kw: SimpleNamespace(count=lambda: 0),
    )
    assert a.upload_via_chooser(page, "Resume", Path("synthetic.pdf"))
    a.upload_in_dialog.assert_called_once_with(page, Path("synthetic.pdf"))


def run_rules_branch(decision, environment):
    """Execute the actual main-loop branch without launching an application."""
    tree = ast.parse(Path(apply_flow.__file__).read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    branch = next(n for n in ast.walk(main) if isinstance(n, ast.If)
                  and isinstance(n.test, ast.Compare) and ast.unparse(n.test) == f"decision == '{decision}'")
    loop = ast.For(target=ast.Name(id="_once", ctx=ast.Store()),
                   iter=ast.List(elts=[ast.Constant(1)], ctx=ast.Load()), body=[branch], orelse=[])
    wrapper = ast.FunctionDef(name="exercise", args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
                              body=[loop], decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    environment.update(decision=decision, logger=logging.getLogger(__name__), RuntimeEvent=None)
    exec(compile(module, apply_flow.__file__, "exec"), environment)
    environment["exercise"]()


def test_rules_continue_clears_checkpoints_without_reader_agent():
    tracker = SimpleNamespace(read_checkpoint=Mock(return_value=None), write_checkpoint=Mock())
    assistant = SimpleNamespace(submission_key="second", click_next_step=Mock(return_value=True))
    run_rules_branch("continue", dict(tracker=tracker, assistant=assistant, key="first", page=object()))
    assert [call.args[0] for call in tracker.read_checkpoint.call_args_list] == ["first", "second"]
    assistant.click_next_step.assert_called_once()


def test_rules_confirmation_computes_status_before_cleanup():
    tracker = SimpleNamespace(update_status=Mock())
    cleanup = Mock()
    run_rules_branch("submitted_by_user", dict(
        assistant=SimpleNamespace(_confirmation_evidence="observed"), page=SimpleNamespace(url="https://jobs.example.com"),
        tracker=tracker, key="first", job_dir=Path("synthetic"),
        diagnostics=SimpleNamespace(capture_safe_screenshot=Mock()),
        safety=SimpleNamespace(verification_status=lambda evidence: ("submitted", "verified")),
        STATUS_SUBMITTED="submitted", delete_screenshots=cleanup, remember_progress=Mock(),
    ))
    tracker.update_status.assert_called_once_with("first", "submitted", notes="verified")
    cleanup.assert_called_once()
