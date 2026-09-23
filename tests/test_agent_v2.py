"""
tests/test_agent_v2.py - Comprehensive test suite for agent_v2 modular architecture.
Verifies encryption, async database CRUD, DOM state signature hashing, constrained dropdown inference,
semantic memory recall, and LangGraph anti-loop cutoff rules.
"""

import asyncio
import tempfile
from pathlib import Path
import pytest

# agent_v2 is an unwired prototype with its own dependencies (requirements-dev.txt).
for _module in ("langgraph", "aiosqlite", "cryptography"):
    pytest.importorskip(_module)

from agent_v2.config import MAX_NODE_ATTEMPTS, AppConfig
from agent_v2.core.parser import DOMParser, FormElement
from agent_v2.graph.state import AutomationState
from agent_v2.graph.workflow import build_automation_graph, router_next_step
from agent_v2.intelligence.inference import InferenceEngine
from agent_v2.intelligence.memory import SemanticQuestionMemory
from agent_v2.storage.db_manager import AsyncDatabaseManager
from agent_v2.storage.encryption import EncryptionManager


# -----------------------------------------------------------------------------
# 1. Config Tests
# -----------------------------------------------------------------------------
def test_config_anti_loop_constant():
    assert MAX_NODE_ATTEMPTS == 3
    cfg = AppConfig.load()
    assert cfg.max_node_attempts == 3


# -----------------------------------------------------------------------------
# 2. Encryption Tests
# -----------------------------------------------------------------------------
def test_encryption_roundtrip():
    crypto = EncryptionManager()
    plain = "SuperSecretAtsPassword123!"
    token = crypto.encrypt(plain)
    assert token != plain
    decrypted = crypto.decrypt(token)
    assert decrypted == plain


def test_encryption_invalid_token():
    crypto = EncryptionManager()
    with pytest.raises(ValueError):
        crypto.decrypt("invalid-tampered-token")


# -----------------------------------------------------------------------------
# 3. Async Database CRUD Tests
# -----------------------------------------------------------------------------
@pytest.mark.anyio
async def test_async_database_crud():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_v2.db"
        db = AsyncDatabaseManager(db_path)
        await db.initialize()

        # Test Identity CRUD
        id_val = await db.save_identity(
            full_name="Yaswanth Jonnalagadda",
            email="yaswanth@example.com",
            phone="+1234567890",
            address="123 Tech Way, Fairfax, VA",
        )
        assert id_val > 0

        identity = await db.get_identity("yaswanth@example.com")
        assert identity is not None
        assert identity["full_name"] == "Yaswanth Jonnalagadda"
        assert identity["phone"] == "+1234567890"

        # Test Credential CRUD
        await db.save_credential(
            domain="jobs.dayforcehcm.com",
            username="candidate@example.com",
            encrypted_password="encrypted_token_example",
            auth_method="password",
        )
        cred = await db.get_credential("jobs.dayforcehcm.com")
        assert cred is not None
        assert cred["username"] == "candidate@example.com"
        assert cred["encrypted_password"] == "encrypted_token_example"

        # Test Form Field Dictionary CRUD
        await db.save_form_answer(
            question_hash="hash_deg_123",
            normalized_question="degree select one required",
            verified_answer="Master's Degree",
            field_type="select",
            domain="verizon.wd12.myworkdayjobs.com",
        )
        ans = await db.get_form_answer("hash_deg_123")
        assert ans is not None
        assert ans["verified_answer"] == "Master's Degree"


# -----------------------------------------------------------------------------
# 4. Semantic Memory Tests
# -----------------------------------------------------------------------------
@pytest.mark.anyio
async def test_semantic_question_memory():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_memory.db"
        db = AsyncDatabaseManager(db_path)
        await db.initialize()

        memory = SemanticQuestionMemory(db)
        await memory.record_answer(
            question="What is your highest completed education level?",
            verified_answer="Master's Degree",
            field_type="select",
        )

        # Exact recall
        recalled, conf = await memory.recall_answer("What is your highest completed education level?")
        assert recalled == "Master's Degree"
        assert conf == 1.0

        # Semantically similar rephrased question
        recalled_sim, conf_sim = await memory.recall_answer("Highest education level completed?")
        assert recalled_sim == "Master's Degree"
        assert conf_sim >= 0.70


# -----------------------------------------------------------------------------
# 5. Inference & Constrained Choice Selection Tests
# -----------------------------------------------------------------------------
@pytest.mark.anyio
async def test_inference_constrained_choice():
    inference = InferenceEngine()
    profile = {
        "first_name": "Yaswanth",
        "last_name": "Jonnalagadda",
        "email": "yaswanth@example.com",
    }

    # Test direct match
    el_name = FormElement(
        tag="input", role="textbox", type="text", id="first_name_input",
        name="firstName", label="First Name", placeholder="First Name",
        required=True, value="", checked=False
    )
    ans = await inference.decide_answer(el_name, profile)
    assert ans == "Yaswanth"

    # Test constrained dropdown selection
    el_dropdown = FormElement(
        tag="select", role="combobox", type="", id="degree_select",
        name="degree", label="Highest Degree Earned", placeholder="",
        required=True, value="", checked=False,
        options=[
            {"value": "1", "text": "High School Diploma"},
            {"value": "2", "text": "Bachelor's Degree"},
            {"value": "3", "text": "Master's Degree"},
            {"value": "4", "text": "Doctorate / Ph.D."},
        ]
    )
    # With historical answer
    ans_dropdown = await inference.decide_answer(el_dropdown, profile, historical_answer="Master's Degree")
    assert ans_dropdown == "Master's Degree"


# -----------------------------------------------------------------------------
# 6. LangGraph State Machine & Loop Guard Cutoff Tests
# -----------------------------------------------------------------------------
def test_langgraph_loop_guard_cutoff():
    # Verify graph compiles without errors
    graph = build_automation_graph()
    assert graph is not None

    # Test Router logic under normal state
    normal_state: AutomationState = {
        "target_url": "https://example.com/apply",
        "job_title": "Engineer",
        "company": "Acme",
        "profile": {},
        "tailored_resume_path": None,
        "current_page_signature": "sig_page_1",
        "previous_signatures": ["sig_page_0"],
        "headings": ["Step 1"],
        "elements": [],
        "answers_queue": [],
        "filled_count": 0,
        "node_visit_counts": {"parse_dom": 1},
        "consecutive_stuck_cycles": 0,
        "max_node_attempts": MAX_NODE_ATTEMPTS,
        "auth_state": "authenticated",
        "stage": "form_filling",
        "status_notes": "",
        "error_message": None,
        "runtime_context": {},
    }
    assert router_next_step(normal_state) == "parse_dom"

    # Test Router logic when loop threshold is exceeded
    stuck_state: AutomationState = dict(normal_state)
    stuck_state["consecutive_stuck_cycles"] = MAX_NODE_ATTEMPTS
    assert router_next_step(stuck_state) == "fallback_recovery"

