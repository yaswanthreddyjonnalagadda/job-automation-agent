"""
agent_v2/main.py - Main entry point script for the autonomous browser agent platform.
Parses CLI parameters, prepares database schemas, initializes components,
and executes the compiled LangGraph state workflow.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_v2.config import MAX_NODE_ATTEMPTS, config
from agent_v2.core.browser import AsyncBrowserSession
from agent_v2.graph.state import AutomationState
from agent_v2.graph.workflow import build_automation_graph
from agent_v2.intelligence.inference import InferenceEngine
from agent_v2.intelligence.llm_client import LLMClient
from agent_v2.intelligence.memory import SemanticQuestionMemory
from agent_v2.storage.db_manager import AsyncDatabaseManager
from agent_v2.storage.encryption import EncryptionManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("agent_v2.main")


async def run_pipeline(
    url: str,
    profile_data: dict,
    headless: bool = False,
    resume_path: str = "",
) -> AutomationState:
    """Initializes infrastructure and executes the LangGraph state machine."""
    logger.info("Initializing agent_v2 platform for %s", url)

    # 1. Initialize Storage & Crypto
    db = AsyncDatabaseManager(config.db_path)
    await db.initialize()
    crypto = EncryptionManager(config.encryption_key)

    # Save identity to local DB
    if "email" in profile_data:
        await db.save_identity(
            full_name=profile_data.get("full_name", profile_data.get("first_name", "") + " " + profile_data.get("last_name", "")),
            email=profile_data["email"],
            phone=profile_data.get("phone"),
            address=profile_data.get("address"),
            linkedin_url=profile_data.get("linkedin_url"),
            github_url=profile_data.get("github_url"),
            portfolio_url=profile_data.get("portfolio_url"),
            raw_profile_json=json.dumps(profile_data),
        )

    # 2. Initialize Intelligence Components
    llm = LLMClient()
    inference = InferenceEngine(llm_client=llm)
    memory = SemanticQuestionMemory(db_manager=db)

    # 3. Launch Async Browser Session
    session = AsyncBrowserSession(
        headless=headless,
        user_data_dir=config.user_data_dir,
    )
    page = await session.start()

    try:
        # 4. Construct Initial LangGraph State
        initial_state: AutomationState = {
            "target_url": url,
            "job_title": profile_data.get("target_title", "Candidate Application"),
            "company": profile_data.get("target_company", "Employer"),
            "profile": profile_data,
            "tailored_resume_path": resume_path if resume_path and Path(resume_path).is_file() else None,
            "current_page_signature": "",
            "previous_signatures": [],
            "headings": [],
            "elements": [],
            "answers_queue": [],
            "filled_count": 0,
            "node_visit_counts": {},
            "consecutive_stuck_cycles": 0,
            "max_node_attempts": MAX_NODE_ATTEMPTS,
            "auth_state": "init",
            "stage": "init",
            "status_notes": "",
            "error_message": None,
            "runtime_context": {
                "page": page,
                "browser_session": session,
                "db": db,
                "crypto": crypto,
                "llm": llm,
                "inference": inference,
                "memory": memory,
            },
        }

        # 5. Compile and Invoke LangGraph Workflow
        app = build_automation_graph()
        logger.info("Executing compiled state machine graph...")
        final_state = await app.ainvoke(initial_state)

        logger.info(
            "State machine execution finished. Final Stage: %s | Notes: %s",
            final_state.get("stage"),
            final_state.get("status_notes") or final_state.get("error_message") or "N/A",
        )
        return final_state

    finally:
        await session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="agent_v2 Autonomous Browser Automation Platform")
    parser.add_argument("url", nargs="?", default="", help="Target job application URL")
    parser.add_argument("--url", dest="opt_url", help="Target URL (flag option)")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--resume", default="", help="Path to base candidate resume")
    parser.add_argument("--profile", default="", help="Optional path to profile JSON file")
    parser.add_argument("--email", default="", help="Candidate email")
    parser.add_argument("--name", default="", help="Candidate full name")
    parser.add_argument("--phone", default="", help="Candidate phone number")

    args = parser.parse_args()
    target_url = args.url or args.opt_url
    if not target_url:
        print("Usage: python -m agent_v2.main <URL> [options]")
        sys.exit(1)

    profile_data = {}
    if args.profile and Path(args.profile).is_file():
        profile_data = json.loads(Path(args.profile).read_text(encoding="utf-8"))

    if args.email:
        profile_data["email"] = args.email
    if args.name:
        profile_data["full_name"] = args.name
    if args.phone:
        profile_data["phone"] = args.phone

    profile_data.setdefault("email", config.ats_email)
    profile_data.setdefault("full_name", "Yaswanth Reddy Jonnalagadda")

    asyncio.run(
        run_pipeline(
            url=target_url,
            profile_data=profile_data,
            headless=args.headless or config.headless,
            resume_path=args.resume,
        )
    )


if __name__ == "__main__":
    main()

