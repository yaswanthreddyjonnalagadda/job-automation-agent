"""Matching and scoring algorithm for newly polled jobs against user_profile.yaml.
Implements the exact 6-stage scoring algorithm:
1. Title match (0-40)
2. Location match (0-20)
3. Salary match (0-15)
4. Keyword match (0-15)
5. Disqualifier check (-100 -> score 0, skipped)
6. Freshness bonus (0-10)
Threshold: >=60 -> queue, 40-59 -> review, <40 -> skip
"""
from __future__ import annotations

import logging
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.diff_engine.storage import JobStorage

logger = logging.getLogger("matcher.scoring")


def _parse_simple_yaml(text: str) -> Dict[str, Any]:
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except ImportError:
        pass
    data: Dict[str, Any] = {}
    current_section = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if stripped.startswith("- "):
            val = stripped[2:].strip().strip('"').strip("'")
            target = data.get(current_section) if current_section in data and isinstance(data[current_section], list) else None
            if target is None:
                data[current_section] = [val]
            else:
                target.append(val)
        elif ":" in stripped:
            k, _, v = stripped.partition(":")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            parsed_val: Any = v
            if v:
                if v.isdigit():
                    parsed_val = int(v)
                elif v.lower() == "true":
                    parsed_val = True
                elif v.lower() == "false":
                    parsed_val = False

            if indent > 0 and current_section:
                if not isinstance(data.get(current_section), dict):
                    data[current_section] = {}
                data[current_section][k] = parsed_val
            else:
                current_section = k
                if v:
                    data[k] = parsed_val
                else:
                    data[k] = {}
    return data


def load_user_profile(path: Optional[str] = None) -> Dict[str, Any]:
    profile_path = path or os.path.join(os.path.dirname(__file__), "..", "..", "config", "user_profile.yaml")
    if os.path.exists(profile_path):
        with open(profile_path, "r", encoding="utf-8") as f:
            return _parse_simple_yaml(f.read())
    return {
        "target_roles": ["Network Engineer", "Senior Network Engineer", "Cloud Network Engineer"],
        "preferred_locations": ["Remote", "Fairfax, VA", "Arlington, VA", "New Jersey", "New York, NY"],
        "salary_range": {"min": 120000, "max": 180000, "currency": "USD"},
        "must_have_keywords": ["network", "cloud", "infrastructure"],
        "nice_to_have_keywords": ["AWS", "Azure", "Cisco", "Python", "Kubernetes"],
        "disqualifiers": ["security clearance", "public trust", "TS/SCI", "US citizen only", "no sponsorship", "cannot sponsor"],
    }


def score_job(job: Dict[str, Any], profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    prof = profile or load_user_profile()
    title = (job.get("title") or "").strip()
    location = (job.get("location") or "").strip()
    description = (job.get("description") or "").strip().lower()
    full_text = f"{title} {location} {description}".lower()

    # 5. Disqualifier check (-100 points -> score = 0, status = 'skipped')
    disqualifiers = prof.get("disqualifiers", [])
    for dq in disqualifiers:
        if dq.lower() in full_text:
            return {
                "score": 0.0,
                "status": "skipped",
                "disqualified_by": dq,
                "reasons": [f"Disqualified by: {dq}"],
            }

    score = 0.0
    reasons: List[str] = []

    # 1. Title match (0-40 points)
    target_roles = prof.get("target_roles", [])
    exact_title_match = any(role.lower() == title.lower() for role in target_roles)
    contains_key_terms = any(term in title.lower() for term in ["network", "cloud", "engineer"])
    adjacent_role = any(adj in title.lower() for adj in ["devops", "sre", "infrastructure", "systems"])

    if exact_title_match:
        score += 40
        reasons.append("Exact target role match (+40)")
    elif contains_key_terms:
        score += 25
        reasons.append("Contains key role terms (+25)")
    elif adjacent_role:
        score += 15
        reasons.append("Adjacent infrastructure role (+15)")

    # 2. Location match (0-20 points)
    preferred_locs = prof.get("preferred_locations", [])
    is_remote = bool(job.get("remote")) or bool(re.search(r"\b(remote|anywhere|virtual)\b", f"{title} {location}", re.I))
    preferred_city = any(loc.lower() in location.lower() for loc in preferred_locs if loc.lower() != "remote")

    if is_remote:
        score += 20
        reasons.append("Remote role (+20)")
    elif preferred_city:
        score += 20
        reasons.append(f"Preferred location match ({location}) (+20)")
    elif re.search(r"\b(va|virginia|md|maryland|dc|nj|new jersey|ny|new york)\b", location, re.I):
        score += 10
        reasons.append(f"Same region/state as preferred ({location}) (+10)")
    elif location:
        score += 5
        reasons.append("Other US location (+5)")

    # 3. Salary match (0-15 points)
    salary_cfg = prof.get("salary_range", {})
    if isinstance(salary_cfg, dict):
        min_target = salary_cfg.get("min", 120000)
    else:
        min_target = prof.get("min", 120000)
    job_min = job.get("salary_min")
    job_max = job.get("salary_max")

    if job_max and job_max >= min_target:
        score += 15
        reasons.append(f"Salary meets target range (+15)")
    elif job_max and job_max >= (min_target * 0.85):
        score += 8
        reasons.append("Salary within 15% of target (+8)")
    elif job_min or job_max:
        reasons.append("Salary below target (+0)")
    else:
        score += 10  # Neutral when not listed
        reasons.append("Salary not listed (neutral +10)")

    # 4. Keyword match (0-15 points)
    must_haves = prof.get("must_have_keywords", [])
    nice_to_haves = prof.get("nice_to_have_keywords", [])

    must_found = sum(1 for kw in must_haves if kw.lower() in full_text)
    nice_found = sum(1 for kw in nice_to_haves if kw.lower() in full_text)

    keyword_pts = min(15, (must_found * 5) + (nice_found * 2))
    score += keyword_pts
    reasons.append(f"Keywords matched: {must_found} must-have, {nice_found} nice-to-have (+{keyword_pts})")

    # 6. Freshness bonus (0-10 points)
    posted_at = job.get("posted_at")
    freshness_pts = 4  # default moderate freshness
    if posted_at:
        try:
            if isinstance(posted_at, str):
                posted_dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
            else:
                posted_dt = posted_at
            age_days = (datetime.now(timezone.utc) - posted_dt).total_seconds() / 86400.0
            if age_days <= 1:
                freshness_pts = 10
            elif age_days <= 3:
                freshness_pts = 7
            elif age_days <= 7:
                freshness_pts = 4
            else:
                freshness_pts = 0
        except Exception:
            freshness_pts = 4
    score += freshness_pts
    reasons.append(f"Freshness bonus (+{freshness_pts})")

    status = "queue" if score >= 60 else ("review" if score >= 40 else "skip")

    return {
        "score": round(score, 1),
        "status": status,
        "reasons": reasons,
    }


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Initializing Matcher daemon...")
    storage = JobStorage()
    profile = load_user_profile()

    redis_client = None
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            import redis
            redis_client = redis.from_url(redis_url, decode_responses=True)
            redis_client.ping()
            logger.info("Connected to Redis at %s", redis_url)
        except Exception as exc:
            logger.warning("Redis connection failed (%s); fallback to DB queries", exc)
            redis_client = None

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        logger.info("Signal %s received; stopping Matcher daemon gracefully...", sig)
        running = False

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    match_interval = int(os.getenv("MATCH_INTERVAL_SECONDS", "15"))
    logger.info("Matcher daemon started with interval %ds", match_interval)

    while running:
        jobs_to_process: List[Dict[str, Any]] = []

        if redis_client:
            try:
                job_id_str = redis_client.lpop("jobs:matching")
                if job_id_str:
                    job = storage.get_job_by_id(int(job_id_str))
                    if job:
                        jobs_to_process.append(job)
            except Exception as exc:
                logger.warning("Error reading from Redis jobs:matching: %s", exc)

        if not jobs_to_process:
            jobs_to_process = storage.get_unmatched_jobs(limit=10)

        if not jobs_to_process:
            elapsed = 0
            while running and elapsed < match_interval:
                time.sleep(1)
                elapsed += 1
            continue

        for job in jobs_to_process:
            job_id = job.get("id")
            title = job.get("title", "Untitled")
            company = job.get("company_slug", "Unknown")
            try:
                result = score_job(job, profile)
                score = result.get("score", 0.0)
                status = result.get("status", "skip")

                storage.update_match_score(job_id, score)
                logger.info("Scored job #%s '%s' at %s: %.1f points (%s)", job_id, title, company, score, status)

                if status == "queue":
                    # Tailor resume and generate cover letter
                    try:
                        from services.matcher.resume_tailor import tailor_resume, generate_cover_letter
                        tailored_pdf, cost, tokens = tailor_resume(
                            job.get("description", ""),
                            title,
                        )
                        cover_letter, _, _ = generate_cover_letter(
                            job.get("description", ""),
                            title,
                            company,
                        )
                    except Exception as tail_exc:
                        logger.error("Failed to tailor resume for job #%s: %s", job_id, tail_exc)
                        tailored_pdf, cost, tokens = "storage/resumes/default.pdf", 0.0, 0
                        cover_letter = ""

                    app_id = storage.create_application({
                        "job_id": job_id,
                        "match_score": score,
                        "resume_version": tailored_pdf,
                        "cover_letter": cover_letter,
                        "tailoring_prompt": f"Tailored for {title} at {company}",
                        "claude_tokens_used": tokens,
                        "claude_cost_usd": cost,
                        "status": "queued",
                        "portal_used": job.get("ats_platform"),
                    })
                    logger.info("Application queued #%s for job #%s", app_id, job_id)

                    if redis_client:
                        try:
                            redis_client.rpush("applications:queued", str(app_id))
                        except Exception as r_exc:
                            logger.warning("Failed to push to Redis applications:queued: %s", r_exc)

                elif status == "review":
                    storage.update_job_status(job_id, "review")
                else:
                    storage.update_job_status(job_id, "skipped")

            except Exception as exc:
                logger.error("Error processing match for job #%s: %s", job_id, exc, exc_info=True)

    logger.info("Matcher daemon shutdown complete.")


if __name__ == "__main__":
    main()
