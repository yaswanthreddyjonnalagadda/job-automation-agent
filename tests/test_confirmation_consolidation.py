"""Every receipt reader uses the one shared definition (consolidation of f297's leftovers).

f297 moved the waiting check onto ``confirmation_text.says_received`` but left two
lists behind: each ``sites/*.py`` ``confirmation_phrases`` tuple (read by nobody)
and ``SubmissionProbe``'s own twelve-phrase list. Comparing those lists with the
shared definition turned up real receipt wording it missed:

* "Application was successfully submitted" (Ashby's receipt),
* "Application has been sent" / "Application was sent" (the probe's list),

while three loose entries correctly stay NOT receipts: "thank you for your
interest" alone (HonorHealth false-submitted, f297), "already applied" (a status
chip) and "your journey with" (marketing prose).
"""
import confirmation_text


def test_ashby_receipt_is_a_receipt():
    assert confirmation_text.says_received("Application was successfully submitted")


def test_bare_application_sent_wordings_are_receipts():
    assert confirmation_text.says_received("Your application has been sent")
    assert confirmation_text.says_received("Application has been sent")
    assert confirmation_text.says_received("Application was sent")


def test_loose_adapter_words_stay_not_receipts():
    assert not confirmation_text.says_received("Thank you for your interest in Acme")
    assert not confirmation_text.says_received("Already applied")
    assert not confirmation_text.says_received("Your journey with Acme starts here")


class _Body:
    def __init__(self, text):
        self._text = text

    def inner_text(self, timeout=None):
        return self._text


class _Page:
    def __init__(self, text):
        self._text = text
        self.url = "https://example.test/confirmation"

    def locator(self, _selector):
        return _Body(self._text)

    def title(self):
        return "Confirmation"

    def evaluate(self, _script):
        return None


class _Tracker:
    def get_submission_effect_state(self, _key):
        return "DISPATCHED"

    def submission_safety_events(self, _key):
        return []


def test_probe_reports_the_shared_definition_not_its_own_list():
    from submission_guard import SubmissionProbe

    snapshot = SubmissionProbe.inspect(
        _Page("Application was successfully submitted.\nWe will be in touch soon."),
        _Tracker(),
        "application-identity",
    )
    assert snapshot["confirmation_text"] == "application was successfully submitted."


def test_probe_reports_nothing_for_a_status_chip():
    from submission_guard import SubmissionProbe

    snapshot = SubmissionProbe.inspect(_Page("Already applied"), _Tracker(), "application-identity")
    assert snapshot["confirmation_text"] == ""


def test_no_adapter_keeps_its_own_confirmation_list():
    import pathlib

    leftovers = [
        path.name
        for path in sorted((pathlib.Path(__file__).resolve().parent.parent / "sites").glob("*.py"))
        if "confirmation_phrases" in path.read_text(encoding="utf-8")
    ]
    assert leftovers == []
