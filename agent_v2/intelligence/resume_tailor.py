"""
intelligence/resume_tailor.py - Tailors resume content to a specific job description and compiles clean PDF.
Ensures factual consistency while emphasizing relevant keywords and technologies.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from agent_v2.intelligence.llm_client import LLMClient

logger = logging.getLogger("agent_v2.resume_tailor")

TAILOR_SYSTEM_PROMPT = """
You are an expert executive resume optimizer.
Your goal: Given a candidate's verified background and a target Job Description (JD), adjust the emphasis, summary, and bullet points to best align with the JD requirements.

CRITICAL RULES:
1. NEVER invent employers, job titles, degrees, dates, or certifications.
2. Only highlight and rephrase real experience from the provided resume text.
3. Emphasize keywords, cloud/security skills, and architectural tools that exist in the candidate's real history.
4. Output structured markdown with sections: Summary, Core Competencies, Professional Experience, Education.
"""


class ResumeTailor:
    """Extracts JD requirements, tailors resume text via LLM, and produces upload-ready PDFs."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or LLMClient()

    async def tailor(
        self,
        base_resume_text: str,
        job_description: str,
        job_title: str,
        company: str,
        output_dir: Path,
    ) -> Path:
        """Tailor resume text and compile to a formatted PDF."""
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_company = "".join(c for c in company if c.isalnum() or c in "._- ").strip().replace(" ", "_")
        safe_title = "".join(c for c in job_title if c.isalnum() or c in "._- ").strip().replace(" ", "_")
        pdf_filename = f"Resume_{safe_company}_{safe_title}.pdf"
        output_pdf_path = output_dir / pdf_filename

        prompt = f"""
Candidate Background Resume:
\"\"\"
{base_resume_text}
\"\"\"

Target Position: {job_title} at {company}
Target Job Description:
\"\"\"
{job_description}
\"\"\"

Generate an optimized, ATS-tailored resume based exclusively on the candidate's real experience.
"""
        logger.info("Tailoring resume for %s @ %s via %s...", job_title, company, self.llm.provider)
        tailored_markdown = await self.llm.complete(prompt, system_prompt=TAILOR_SYSTEM_PROMPT, max_tokens=2500)

        # Compile PDF from tailored text
        self.compile_pdf(tailored_markdown, output_pdf_path, title=f"{job_title} - {company}")
        logger.info("Compiled tailored PDF at %s", output_pdf_path)
        return output_pdf_path

    @staticmethod
    def compile_pdf(text_content: str, target_path: Path, title: str = "Resume") -> None:
        """Renders plain text/markdown into an elegant single/two-page PDF using reportlab."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(
            str(target_path),
            pagesize=letter,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36,
        )

        styles = getSampleStyleSheet()
        normal = styles["Normal"]
        normal.fontSize = 9.5
        normal.leading = 13

        heading_style = ParagraphStyle(
            "HeadingStyle",
            parent=styles["Heading2"],
            fontSize=12,
            leading=16,
            spaceAfter=4,
            spaceBefore=8,
            textColor="#1a365d",
        )

        title_style = ParagraphStyle(
            "TitleStyle",
            parent=styles["Title"],
            fontSize=16,
            leading=20,
            spaceAfter=10,
            textColor="#0f172a",
        )

        story = []
        lines = text_content.splitlines()
        for line in lines:
            line_str = line.strip()
            if not line_str:
                story.append(Spacer(1, 4))
                continue
            if line_str.startswith("# "):
                story.append(Paragraph(line_str[2:].strip(), title_style))
            elif line_str.startswith("## ") or line_str.startswith("### "):
                story.append(Paragraph(line_str.lstrip("#").strip(), heading_style))
            elif line_str.startswith("- ") or line_str.startswith("* "):
                bullet_text = line_str[2:].strip()
                story.append(Paragraph(f"&bull; {bullet_text}", normal))
            else:
                story.append(Paragraph(line_str, normal))

        doc.build(story)

