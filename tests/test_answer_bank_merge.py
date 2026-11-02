"""The answer bank only grows: a smaller tracker cannot shrink it (21-24 October 2026).

The bank was deleted and rebuilt from whichever tracker was open, so a run that fell back to a smaller SQLite file
dropped answers learned earlier. It also learned only from sent applications, so an answer the agent gave on one held
for the owner's review was never reused.
"""
from types import SimpleNamespace

import answer_bank

Q = "How many years of experience do you have with network automation tools?"
Q2 = "Are you comfortable working on call rotations outside business hours?"


class FakeTracker:
    def __init__(self, apps):
        self.apps = apps

    def list_all(self):
        return [SimpleNamespace(id=i, company=a.get("company", "Acme"), status=a["status"], updated_at=a.get("when", ""))
                for i, a in enumerate(self.apps)]

    def answers_for(self, app_id):
        return self.apps[app_id]["rows"]


def row(question, answer, by="verified_agent"):
    return {"question": question, "answer": answer, "answered_by": by, "host": "boards.example.com"}


def build(tmp_path, *apps):
    path = tmp_path / "bank.json"
    answer_bank.rebuild(FakeTracker(list(apps)), path)
    return path


def test_a_smaller_tracker_does_not_shrink_the_bank(tmp_path):
    path = build(tmp_path, {"status": "submitted", "rows": [row(Q, "5"), row(Q2, "Yes")]})
    assert answer_bank.lookup(Q, path=path)["value"] == "5"
    answer_bank.rebuild(FakeTracker([]), path)                       # the database that was down: nothing in it
    answer_bank._cache["mtime"] = None
    assert answer_bank.lookup(Q, path=path)["value"] == "5"
    assert answer_bank.lookup(Q2, path=path)["value"] == "Yes"


def test_an_answer_for_review_is_learned_but_ranks_below_a_confirmed_one(tmp_path):
    path = build(tmp_path, {"status": "needs_user_review", "when": "2026-10-20", "rows": [row(Q, "7")]})
    assert answer_bank.lookup(Q, path=path)["source"] == "reviewed"
    answer_bank._cache["mtime"] = None
    # An older sent application still wins over a newer reviewed one.
    path = build(tmp_path, {"status": "submitted", "when": "2026-10-01", "rows": [row(Q, "5")]},
                 {"status": "needs_user_review", "when": "2026-10-22", "rows": [row(Q, "7")]})
    answer_bank._cache["mtime"] = None
    entry = answer_bank.lookup(Q, path=path)
    assert (entry["value"], entry["source"]) == ("5", "confirmed") and entry["others"] == ["7"]


def test_the_owners_answer_beats_the_agents_and_a_newer_one_replaces_an_older(tmp_path):
    path = build(tmp_path, {"status": "submitted", "when": "2026-10-01", "rows": [row(Q, "5")]})
    answer_bank.rebuild(FakeTracker([{"status": "skipped", "when": "2026-10-23", "rows": [row(Q, "9", "user")]}]), path)
    answer_bank._cache["mtime"] = None
    assert answer_bank.lookup(Q, path=path)["value"] == "9"


def test_forgotten_keys_stay_forgotten(tmp_path):
    import json
    path = build(tmp_path, {"status": "submitted", "rows": [row(Q, "5")]})
    data = json.loads(path.read_text(encoding="utf-8"))
    data["forgotten"] = [answer_bank.key(Q)]
    path.write_text(json.dumps(data), encoding="utf-8")
    answer_bank.rebuild(FakeTracker([{"status": "submitted", "rows": [row(Q, "5")]}]), path)
    answer_bank._cache["mtime"] = None
    assert answer_bank.lookup(Q, path=path) is None


def test_the_refusals_still_apply_to_reviewed_answers_and_to_what_was_kept(tmp_path):
    attestation = "I certify that the information I have provided is true and complete"
    path = build(tmp_path, {"status": "needs_user_review", "rows": [row(attestation, "Yes"), row(Q, "5")]})
    answer_bank._cache["mtime"] = None
    assert answer_bank.lookup(attestation, path=path) is None
    assert answer_bank.lookup(Q, path=path)["value"] == "5"


def test_page_copy_and_the_models_nothing_to_say_are_never_learned(tmp_path):
    rows = [row("for more details.", "Yes"), row("If you answer yes to this question, you will be required to provide an explanation", "No"),
            row("Do you hold any academic scholarships or awards at present?", "Not specified in the candidate's profile or resume."),
            row(Q, "5")]
    path = build(tmp_path, {"status": "needs_user_review", "rows": rows})
    entries = answer_bank._read(path)["entries"]
    assert [e["value"] for e in entries.values()] == ["5"]
