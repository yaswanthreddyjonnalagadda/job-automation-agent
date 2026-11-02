from types import SimpleNamespace

import pytest

import apply_flow


@pytest.mark.parametrize("correction", ["PA-5200", RuntimeError("provider failed"), "Network engineer"])
def test_only_clean_corrections_can_become_pdfs(tmp_path, monkeypatch, correction):
    replies = iter(["Network engineer PA-5200 CCNP", correction])

    def write(*args, **kwargs):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(apply_flow, "ensure_resume_header", lambda text, *args: text)
    monkeypatch.setattr(apply_flow, "normalise_contact_details", lambda text, *args: text)
    monkeypatch.setattr(apply_flow, "strip_model_preamble", lambda text, *args: text)
    built = []
    monkeypatch.setattr(apply_flow, "build_resume_pdf", lambda *args: built.append(args))
    txt, pdf = tmp_path / "resume.txt", tmp_path / "resume.pdf"
    result = apply_flow._tailor_with(
        SimpleNamespace(tailor_resume=write), SimpleNamespace(raw_text="Network engineer"),
        SimpleNamespace(company="Example"), SimpleNamespace(), tmp_path, txt, pdf, "Test",
    )
    if correction == "Network engineer":
        assert result == pdf
        assert txt.read_text() == correction
        assert len(built) == 1
    else:
        assert result is None
        assert not txt.exists()
        assert not built
