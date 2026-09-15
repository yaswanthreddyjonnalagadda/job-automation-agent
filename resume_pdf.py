"""
Renders a tailored resume (plain text, as produced by
claude_integration.tailor_resume) into a clean PDF suitable for uploading
to an application form.

The text format this expects is what the tailoring prompt produces:
    line 1: NAME
    line 2: headline/title
    line 3: contact line
    then ALL-CAPS section headers (SUMMARY, SKILLS, EXPERIENCE, ...),
    "- " bullets, and "Company | Dates | Title" job header lines.
"""

from __future__ import annotations

import logging
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate

logger = logging.getLogger(__name__)

SECTION_HEADERS = {"SUMMARY", "SKILLS", "EXPERIENCE", "EDUCATION", "CERTIFICATIONS", "PROJECTS"}
_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December", "Present",
)


def build_resume_pdf(text_path: str | Path, pdf_path: str | Path) -> Path:
    text_path, pdf_path = Path(text_path), Path(pdf_path)
    lines = [l.rstrip() for l in text_path.read_text(encoding="utf-8").splitlines()]
    if len(lines) < 3:
        raise ValueError(f"Resume text looks too short to render: {text_path}")

    # The first three lines become the name, headline and contact block. If a
    # section heading lands in the name slot, the source text has no header and
    # rendering it produces a resume with no name and no contact details --
    # refuse loudly instead of shipping that to an employer.
    if lines[0].strip().lower().rstrip(":") in {
        "summary", "profile", "objective", "experience", "skills", "education",
        "professional summary", "work experience", "technical skills",
    }:
        raise ValueError(
            f"{text_path} starts with a section heading ({lines[0]!r}) instead of a "
            "name/contact header -- refusing to build a resume with no name on it."
        )

    styles = getSampleStyleSheet()
    name_style = ParagraphStyle("Name", parent=styles["Heading1"], fontSize=16, alignment=TA_CENTER, spaceAfter=2)
    title_style = ParagraphStyle("Title", parent=styles["Normal"], fontSize=11, alignment=TA_CENTER,
                                 textColor=colors.HexColor("#333333"), spaceAfter=2)
    contact_style = ParagraphStyle("Contact", parent=styles["Normal"], fontSize=9, alignment=TA_CENTER,
                                   textColor=colors.HexColor("#555555"), spaceAfter=10)
    section_style = ParagraphStyle("Section", parent=styles["Heading2"], fontSize=11,
                                   textColor=colors.HexColor("#1a3d6d"), spaceBefore=10, spaceAfter=4)
    job_header_style = ParagraphStyle("JobHeader", parent=styles["Normal"], fontSize=9.5,
                                      spaceBefore=6, spaceAfter=2, leading=13)
    bullet_style = ParagraphStyle("Bullet", parent=styles["Normal"], fontSize=9.5, leftIndent=14,
                                  spaceAfter=3, leading=13)
    body_style = ParagraphStyle("Body", parent=styles["Normal"], fontSize=9.5, spaceAfter=6, leading=13)

    story = [
        Paragraph(lines[0], name_style),
        Paragraph(lines[1], title_style),
        Paragraph(lines[2], contact_style),
    ]

    for line in lines[3:]:
        line = line.strip()
        if not line:
            continue
        if line.upper() in SECTION_HEADERS:
            story.append(Paragraph(line, section_style))
        elif line.startswith("- "):
            story.append(Paragraph("&bull;&nbsp;&nbsp;" + line[2:], bullet_style))
        elif "|" in line and any(m in line for m in _MONTHS):
            story.append(Paragraph(f"<b>{line}</b>", job_header_style))
        else:
            story.append(Paragraph(line, body_style))

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(
        str(pdf_path), pagesize=letter,
        topMargin=0.55 * inch, bottomMargin=0.55 * inch,
        leftMargin=0.65 * inch, rightMargin=0.65 * inch,
    ).build(story)
    logger.info("Wrote resume PDF: %s", pdf_path)
    return pdf_path


def build_letter_pdf(text_path: str | Path, pdf_path: str | Path) -> Path:
    """Renders a plain-text cover letter as a simple one-column PDF: blank
    lines separate paragraphs, single line breaks (the sign-off block) are kept."""
    text_path, pdf_path = Path(text_path), Path(pdf_path)
    text = text_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Cover letter is empty: {text_path}")

    styles = getSampleStyleSheet()
    body_style = ParagraphStyle("LetterBody", parent=styles["Normal"], fontSize=10.5, leading=15, spaceAfter=10)
    story = [
        Paragraph(escape(block.strip()).replace("\n", "<br/>"), body_style)
        for block in text.split("\n\n") if block.strip()
    ]

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(
        str(pdf_path), pagesize=letter,
        topMargin=0.9 * inch, bottomMargin=0.9 * inch,
        leftMargin=1 * inch, rightMargin=1 * inch,
    ).build(story)
    logger.info("Wrote cover letter PDF: %s", pdf_path)
    return pdf_path


if __name__ == "__main__":
    import sys
    build_resume_pdf(sys.argv[1], sys.argv[2])
    print(f"Wrote {sys.argv[2]}")
