"""
Job application co-pilot -- orchestrator.

Workflow (deliberately human-in-the-loop, see SETUP_GUIDE.md for why):
  1. You browse LinkedIn/Indeed/Dice yourself and drop job URLs into
     data/job_queue.txt (one per line), or add JD text via --paste.
  2. For each new (non-duplicate) job:
       a. Parse your resume (cached after first run).
       b. Fetch/accept the JD text, analyze it with Claude.
       c. Generate a tailored resume section + cover letter with Claude.
       d. Save both under output/<company>_<title>/.
       e. Open the job URL in a real, visible browser (Playwright). If it's
          the first time, you log in yourself -- the session persists after.
       f. Auto-fill the fields the assistant can confidently match, upload
          your resume, then STOP and wait for you to review and submit.
       g. Record the outcome in the SQLite tracker so it's never repeated.

Usage:
    python main.py                 # process everything in the job queue
    python main.py --list          # show tracked applications
    python main.py --dry-run       # generate materials only, skip the browser step
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from claude_integration import ClaudeClient, ClaudeIntegrationError
from browser_automation import JobApplicationAssistant
from config import get_app_config, get_user_profile
from jd_analyzer import build_job_description, dedup_key_for_url, eligibility_summary, local_eligibility_flags
from job_tracker import STATUS_FORM_FILLED, STATUS_SKIPPED, JobTracker
from resume_parser import ResumeData, parse_resume


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "agent.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    # Never let API keys or other secrets leak into logs via library debug output.
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


logger = logging.getLogger("main")


def read_job_queue(path: Path) -> list[str]:
    if not path.exists():
        path.write_text(
            "# One job URL per line. Lines starting with # are ignored.\n"
            "# Paste in URLs you found yourself while browsing LinkedIn/Indeed/Dice.\n",
            encoding="utf-8",
        )
        logger.info("Created empty job queue at %s -- add job URLs and re-run.", path)
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def prompt_for_jd_text(url: str) -> tuple[str, str, str, str]:
    """The tool does not scrape job sites. Ask the human to paste in the
    posting details they're already looking at in their browser."""
    print(f"\n--- New job: {url} ---")
    title = input("Job title: ").strip()
    company = input("Company: ").strip()
    location = input("Location: ").strip()
    print("Paste the full job description text, then press Enter and Ctrl+Z/Ctrl+D:")
    raw_text = sys.stdin.read().strip()
    return title, company, location, raw_text


def confirm(prompt: str) -> bool:
    answer = input(f"{prompt} [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def print_warnings(title: str, warnings: list[str]) -> None:
    print(f"\n!!! {title} !!!")
    for w in warnings:
        print(f"  - {w}")


def process_job(
    *,
    url: str,
    tracker: JobTracker,
    claude: ClaudeClient,
    resume: ResumeData,
    profile,
    config,
    dry_run: bool,
) -> None:
    if tracker.is_duplicate(dedup_key_for_url(url)):
        logger.info("Already processed %s, skipping (dedup) -- no need to paste it again", url)
        return

    title, company, location, raw_text = prompt_for_jd_text(url)
    if not raw_text:
        logger.warning("No JD text provided for %s, skipping", url)
        return

    job = build_job_description(
        title=title, company=company, location=location, url=url, raw_text=raw_text
    )

    # --- Checkpoint 1: free, local eligibility scan before any API call ---
    early_flags = local_eligibility_flags(job.raw_text)
    if early_flags and profile.requires_visa_sponsorship:
        print_warnings(f"Possible eligibility issue for {job.title} @ {job.company}", early_flags)
        if not confirm("Proceed with AI analysis of this job anyway?"):
            tracker.create(
                dedup_key=job.dedup_key, title=job.title, company=job.company,
                location=job.location, source_site=job.source_site, url=job.url,
            )
            tracker.update_status(job.dedup_key, STATUS_SKIPPED, notes="; ".join(early_flags))
            logger.info("Skipped %s @ %s at checkpoint 1 (eligibility)", job.title, job.company)
            return

    # Reserve the tracker record now so a later skip still prevents re-processing.
    tracker.create(
        dedup_key=job.dedup_key, title=job.title, company=job.company,
        location=job.location, source_site=job.source_site, url=job.url,
    )

    try:
        claude.analyze_job(job)
    except ClaudeIntegrationError as exc:
        logger.error("Claude JD analysis failed for %s @ %s: %s", job.title, job.company, exc)
        tracker.update_status(job.dedup_key, STATUS_SKIPPED, notes=f"analysis failed: {exc}")
        return

    # --- Checkpoint 2: full eligibility check before spending tokens on tailoring ---
    warnings = eligibility_summary(job, profile)
    if warnings:
        print_warnings(f"Eligibility/fit concerns for {job.title} @ {job.company}", warnings)
        if not confirm("Proceed with tailoring a resume and cover letter for this job?"):
            tracker.update_status(job.dedup_key, STATUS_SKIPPED, notes="; ".join(warnings))
            logger.info("Skipped %s @ %s at checkpoint 2 (eligibility)", job.title, job.company)
            return
    else:
        print(f"\nNo eligibility red flags found for {job.title} @ {job.company}.")

    try:
        tailored_resume_text = claude.tailor_resume(resume, job, profile)
        cover_letter_text = claude.generate_cover_letter(resume, job, profile)
    except ClaudeIntegrationError as exc:
        logger.error("Claude tailoring failed for %s @ %s: %s", job.title, job.company, exc)
        tracker.update_status(job.dedup_key, STATUS_SKIPPED, notes=f"tailoring failed: {exc}")
        return

    job_dir = config.output_dir / f"{company.replace(' ', '_')}_{title.replace(' ', '_')}"
    job_dir.mkdir(parents=True, exist_ok=True)
    resume_out = job_dir / "tailored_resume.txt"
    cover_letter_out = job_dir / "cover_letter.txt"
    resume_out.write_text(tailored_resume_text, encoding="utf-8")
    cover_letter_out.write_text(cover_letter_text, encoding="utf-8")

    tracker.update_materials(job.dedup_key, str(resume_out), str(cover_letter_out))
    logger.info("Prepared materials for %s @ %s -> %s", job.title, job.company, job_dir)

    if dry_run:
        logger.info("Dry run: skipping browser step")
        return

    with JobApplicationAssistant(config) as assistant:
        page = assistant.open_job_page(url)
        print(
            "\nIf this is your first time on this site, log in now in the browser window.\n"
            "Press Enter here once the job application form is visible..."
        )
        input()
        fields = assistant.detect_form_fields(page)
        assistant.fill_detected_fields(page, fields, profile)
        assistant.upload_resume(page, config.resume_path)
        tracker.update_status(job.dedup_key, STATUS_FORM_FILLED)
        # --- Checkpoint 3: explicit checklist before the human clicks Submit ---
        assistant.pause_for_human_review(page, job_title=job.title, company=job.company)


def main() -> None:
    parser = argparse.ArgumentParser(description="Job application co-pilot")
    parser.add_argument("--list", action="store_true", help="List tracked applications and exit")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Generate tailored materials only; skip opening the browser/form-fill step",
    )
    args = parser.parse_args()

    config = get_app_config()
    profile = get_user_profile()
    setup_logging(config.log_dir)

    tracker = JobTracker(config.db_path)

    if args.list:
        for record in tracker.list_all():
            print(f"[{record.status:12}] {record.title} @ {record.company} ({record.url})")
        return

    resume = parse_resume(config.resume_path)
    claude = ClaudeClient(config)

    queue = read_job_queue(config.job_queue_path)
    if not queue:
        logger.info("Job queue is empty. Add URLs to %s and re-run.", config.job_queue_path)
        return

    for url in queue:
        try:
            process_job(
                url=url,
                tracker=tracker,
                claude=claude,
                resume=resume,
                profile=profile,
                config=config,
                dry_run=args.dry_run,
            )
        except KeyboardInterrupt:
            raise
        except Exception:
            logger.exception("Unexpected error processing %s", url)


if __name__ == "__main__":
    main()
