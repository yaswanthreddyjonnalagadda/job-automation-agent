"""A phone alert when the agent needs the owner (ntfy).

Off unless NTFY_TOPIC is set; never raises, so a missing network can never stop an application; and it carries
what is needed (company, job, which kind of help) without any answer the owner typed, a password, a code or an
email address -- the message goes through a third-party server.
"""
import urllib.error

import pytest

import phone_alert


class Captured:
    def __init__(self):
        self.requests = []

    def urlopen(self, request, timeout=None):
        self.requests.append(request)

        class Reply:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"{}"
        return Reply()


@pytest.fixture
def sent(monkeypatch):
    captured = Captured()
    monkeypatch.setattr(phone_alert.urllib.request, "urlopen", captured.urlopen)
    return captured


def test_nothing_is_sent_without_a_topic(sent, monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert phone_alert.configured() is False
    assert phone_alert.send("Needs you", "x") is False
    assert sent.requests == []


def test_a_message_goes_to_the_topic_with_its_title_priority_and_link(sent, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "jaa-example-topic")
    monkeypatch.delenv("NTFY_SERVER", raising=False)
    assert phone_alert.send("Needs you: Copart", "A CAPTCHA is waiting", priority=4, tags=("warning",),
                            click_url="http://100.101.102.103:8765/c/tok/") is True
    request = sent.requests[0]
    assert request.full_url == "https://ntfy.sh/jaa-example-topic"
    assert request.get_method() == "POST"
    assert request.get_header("Title") == "Needs you: Copart"
    assert request.get_header("Priority") == "4"
    assert request.get_header("Tags") == "warning"
    assert request.get_header("Click") == "http://100.101.102.103:8765/c/tok/"
    assert request.data == b"A CAPTCHA is waiting"


def test_a_self_hosted_server_can_be_named(sent, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    monkeypatch.setenv("NTFY_SERVER", "https://ntfy.example.test/")
    phone_alert.send("Hi", "there")
    assert sent.requests[0].full_url == "https://ntfy.example.test/t"


def test_the_title_is_plain_ascii_whatever_the_job_is_called(sent, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    phone_alert.send("Needs you: Zurich – Senior Ingénieur Réseau", "x")
    sent.requests[0].get_header("Title").encode("ascii")     # raises if not ASCII


def test_nothing_private_leaves_the_machine(sent, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    phone_alert.send("Needs you", "required field is still blank: Notice period = '2 weeks'; "
                                  "contact jane@example.invalid or +1-202-555-0199; password=Hunter2!x")
    body = sent.requests[0].data.decode()
    for private in ("2 weeks", "jane@example.invalid", "202-555-0199", "Hunter2"):
        assert private not in body, private
    assert "Notice period" in body            # the question is the site's own words, and is what is needed


def test_a_long_message_is_cut(sent, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    phone_alert.send("Needs you", "word " * 500)
    assert len(sent.requests[0].data) <= 400


@pytest.mark.parametrize("failure", [urllib.error.URLError("offline"), TimeoutError("slow"), OSError("down")])
def test_a_failing_network_never_raises(monkeypatch, failure):
    monkeypatch.setenv("NTFY_TOPIC", "t")

    def boom(*args, **kwargs):
        raise failure
    monkeypatch.setattr(phone_alert.urllib.request, "urlopen", boom)
    assert phone_alert.send("Needs you", "x") is False


@pytest.mark.parametrize("kind,reasons,expected_title,expected_priority", [
    ("captcha", ["a CAPTCHA is on the page"], "CAPTCHA to solve", 5),
    ("owner_needed", ["required field is still blank: Notice period"], "Questions the agent can't answer", 4),
    ("owner_needed", ["the form says something"], "Needs you", 4),
])
def test_what_is_needed_is_named_in_the_alert(kind, reasons, expected_title, expected_priority):
    title, message, priority, _tags = phone_alert.describe_need("Copart, Inc", "Senior Network Engineer", kind, reasons)
    assert title.startswith(expected_title) and "Copart" in title
    assert priority == expected_priority
    assert "Senior Network Engineer" in message
