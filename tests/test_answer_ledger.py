"""The answer ledger works on its own, without the dashboard (moved out of web_ui.py, audit 26 October 2026)."""
import answer_ledger


def test_a_tracker_that_cannot_be_opened_costs_the_mirror_not_the_override(tmp_path, monkeypatch):
    monkeypatch.setattr(answer_ledger, "DB_PATH", tmp_path / "ledger.db")
    row = answer_ledger.record_answer_ledger("key", "Are you authorised to work?", "Yes")

    def no_tracker():
        raise RuntimeError("tracker is down")

    assert answer_ledger.update_answer(row, "No", tracker_factory=no_tracker) is True
    [saved] = answer_ledger.get_answers_for_application("key")
    assert (saved["final_answer"], saved["source"], saved["previous_value"]) == ("No", "USER_OVERRIDE", "Yes")


def test_overriding_an_answer_that_does_not_exist_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(answer_ledger, "DB_PATH", tmp_path / "ledger.db")
    assert answer_ledger.update_answer(999, "No", tracker_factory=lambda: None) is False


def test_web_ui_still_exposes_the_ledger_under_its_old_names():
    import web_ui
    for name in ("init_answer_ledger", "record_answer_ledger", "get_answers_for_application", "update_answer_in_ledger"):
        assert callable(getattr(web_ui, name)), name
