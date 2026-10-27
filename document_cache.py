"""Pre-computed tailored documents (P3-2 follow-on, Phase 2).

Tailoring a resume takes 30-60 s; doing it while the browser waits on the
application page is the slowest part of a run. `precompute_documents` does it
ahead of time and files the PDF under data/cache/<job_hash>/, and
`get_cached_documents` hands it to the runner in a file read.

Nothing is written unless the tailored text passes `safety.unsupported_claims`
against the owner's real resume -- the same bar `apply_flow._tailor_with` sets.
The writer is injected so this module opens no connection of its own.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Callable, Optional

import safety

logger = logging.getLogger(__name__)

CACHE_ROOT = Path(__file__).resolve().parent / "data" / "cache"

# tailor(jd_text, company, title) -> tailored resume text
Tailor = Callable[[str, str, str], str]
# render(text_path, pdf_path) -> pdf path
Render = Callable[[Path, Path], Path]


def job_hash(job_id_or_url: str) -> str:
    return hashlib.sha256((job_id_or_url or "").strip().lower().encode("utf-8")).hexdigest()[:24]


def _folder(job_id_or_url: str, root: Optional[Path]) -> Path:
    return Path(root or CACHE_ROOT) / job_hash(job_id_or_url)


def _whole_pdf(data: bytes) -> bool:
    return data.startswith(b"%PDF-") and b"%%EOF" in data[-1024:]


def precompute_documents(
    job_id_or_url: str,
    jd_text: str,
    company: str,
    title: str,
    *,
    resume_text: str,
    tailor: Tailor,
    render: Optional[Render] = None,
    root: Optional[Path] = None,
) -> Optional[Path]:
    """Tailor, check, render and file the resume. Returns the PDF path, or None
    when the writer failed or its draft claims something the resume does not."""
    folder = _folder(job_id_or_url, root)
    cached = get_cached_documents(job_id_or_url, root=root)
    if cached:
        return cached["resume_pdf"]
    try:
        tailored = tailor(jd_text, company, title)
    except Exception as exc:
        logger.warning("PRECOMPUTE_FAILED for %s: %s", company, exc)
        return None
    if not (tailored or "").strip():
        return None
    unsupported = safety.unsupported_claims(resume_text, tailored)
    if unsupported:
        logger.error("PRECOMPUTE_REJECTED for %s: unsupported claim(s) %s", company, ", ".join(unsupported[:6]))
        return None
    if render is None:
        from resume_pdf import build_resume_pdf as render
    folder.mkdir(parents=True, exist_ok=True)
    text_path, pdf_path = folder / "resume.txt", folder / "resume.pdf"
    text_path.write_text(tailored, encoding="utf-8")
    try:
        render(text_path, pdf_path)
    except Exception as exc:
        logger.warning("PRECOMPUTE_RENDER_FAILED for %s: %s", company, exc)
        pdf_path.unlink(missing_ok=True)
        text_path.unlink(missing_ok=True)
        return None
    return pdf_path


def get_cached_documents(job_id_or_url: str, *, root: Optional[Path] = None) -> Optional[dict]:
    """{"resume_pdf": Path, "resume_text": str} for a whole cached PDF, else None."""
    folder = _folder(job_id_or_url, root)
    pdf, text = folder / "resume.pdf", folder / "resume.txt"
    try:
        if not (pdf.is_file() and text.is_file() and _whole_pdf(pdf.read_bytes())):
            return None
        return {"resume_pdf": pdf, "resume_text": text.read_text(encoding="utf-8")}
    except OSError:
        return None
