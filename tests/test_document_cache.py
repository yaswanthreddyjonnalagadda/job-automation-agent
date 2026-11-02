"""Pre-computed tailored documents (document_cache.py)."""
import document_cache as dc

RESUME = "Jane Doe. Network engineer. BGP and Terraform. 2019 to 2024."


def _render(text_path, pdf_path):
    pdf_path.write_bytes(b"%PDF-1.4\nbody\n%%EOF\n")
    return pdf_path


def test_precompute_files_pdf_and_text_then_serves_from_cache(tmp_path):
    calls = []
    def tailor(jd, company, title):
        calls.append(1)
        return "Jane Doe. BGP and Terraform."
    pdf = dc.precompute_documents("https://x/1", "jd", "Acme", "NE", resume_text=RESUME,
                                  tailor=tailor, render=_render, root=tmp_path)
    assert pdf.is_file()
    got = dc.get_cached_documents("https://x/1", root=tmp_path)
    assert got["resume_pdf"] == pdf and "BGP" in got["resume_text"]
    dc.precompute_documents("https://x/1", "jd", "Acme", "NE", resume_text=RESUME,
                            tailor=tailor, render=_render, root=tmp_path)
    assert len(calls) == 1  # second call never reaches the writer


def test_unsupported_claim_is_never_written(tmp_path):
    out = dc.precompute_documents("u", "jd", "Acme", "NE", resume_text=RESUME,
                                  tailor=lambda *a: "Cut latency 40% and held CCIE.",
                                  render=_render, root=tmp_path)
    assert out is None and dc.get_cached_documents("u", root=tmp_path) is None
    assert not list(tmp_path.rglob("*"))


def test_writer_failure_and_empty_text_return_none(tmp_path):
    def boom(*a):
        raise RuntimeError("no credit")
    assert dc.precompute_documents("a", "jd", "A", "T", resume_text=RESUME, tailor=boom, render=_render, root=tmp_path) is None
    assert dc.precompute_documents("b", "jd", "A", "T", resume_text=RESUME, tailor=lambda *a: "  ", render=_render, root=tmp_path) is None


def test_truncated_pdf_is_not_served(tmp_path):
    dc.precompute_documents("u", "jd", "A", "T", resume_text=RESUME, tailor=lambda *a: "Jane Doe. BGP.", render=_render, root=tmp_path)
    (tmp_path / dc.job_hash("u") / "resume.pdf").write_bytes(b"%PDF-1.4 cut")
    assert dc.get_cached_documents("u", root=tmp_path) is None


def test_render_failure_leaves_nothing_behind(tmp_path):
    def bad(t, p):
        raise OSError("disk")
    assert dc.precompute_documents("u", "jd", "A", "T", resume_text=RESUME, tailor=lambda *a: "Jane Doe. BGP.", render=bad, root=tmp_path) is None
    assert dc.get_cached_documents("u", root=tmp_path) is None
