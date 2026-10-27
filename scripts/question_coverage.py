"""Read application questions ahead of time and measure how many the agent can already handle.

    python scripts/question_coverage.py harvest   # public Greenhouse boards: GET ...?questions=true, no login, nothing submitted
    python scripts/question_coverage.py score     # offline: how the agent's own code would treat each question

Greenhouse's Job Board API lists every question of a job, with the options of each select, to anyone ("Job Board data is
publicly available, so authentication is not required for any GET endpoints"). Reading them before an application starts
finds the wordings the agent does not know yet without waiting for a real application to stop on one. The corpus
(reference/question_corpus.json) holds question text, field type and option labels only -- nothing about a job or a person.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "reference" / "question_corpus.json"
EXTRA_BOARDS = ["airbnb", "dropbox", "lyft", "twilio", "gitlab", "robinhood", "instacart", "databricks", "affirm", "brex",
                "asana", "discord", "anthropic", "elastic", "hubspot", "squarespace"]
SKIP_TYPES = {"input_file", "input_hidden"}      # documents have their own step; hidden fields are not questions
JOBS_PER_BOARD = 5
ESSAY = re.compile(r"\b(?:why|describe|tell us|explain|accomplish\w*|project|share)\b", re.IGNORECASE)
POLICY = re.compile(r"privacy|acknowledg|consent|agree|certif|attest|arbitrat|i understand", re.IGNORECASE)


def _get(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "job-agent-question-coverage/1.0"})
    with urllib.request.urlopen(request, timeout=20) as reply:
        return json.loads(reply.read().decode("utf-8", "replace"))


def _boards() -> list[str]:
    tokens = []
    try:
        for entry in json.loads((ROOT / "data" / "target_companies.json").read_text(encoding="utf-8")):
            if entry.get("ats") == "greenhouse":
                tokens.append(entry["token"])
    except (OSError, ValueError):
        pass
    return list(dict.fromkeys(tokens + EXTRA_BOARDS))


def _norm(label: str) -> str:
    return re.sub(r"\s+", " ", label or "").strip()


def _questions_of(job: dict) -> list[dict]:
    found = []
    groups = [job.get("questions") or []]
    for key in ("location_questions", "compliance", "demographic_questions"):
        value = job.get(key)
        if isinstance(value, list):
            groups.append(value)
            for item in value:
                if isinstance(item, dict) and isinstance(item.get("questions"), list):
                    groups.append(item["questions"])
    for group in groups:
        for q in group:
            if not isinstance(q, dict):
                continue
            label, fields = _norm(q.get("label")), q.get("fields") or []
            if not label or not fields:
                continue
            first = fields[0]
            if first.get("type") in SKIP_TYPES:
                continue
            found.append({"label": label, "type": first.get("type", ""), "required": bool(q.get("required")),
                          "options": [_norm(v.get("label")) for v in (first.get("values") or [])][:60]})
    return found


def harvest() -> None:
    seen: dict[str, dict] = {}
    if CORPUS.is_file():
        for row in json.loads(CORPUS.read_text(encoding="utf-8")).get("questions", []):
            seen[row["label"].lower()[:140]] = row
    jobs_read = 0
    for token in _boards():
        try:
            jobs = _get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs").get("jobs", [])
        except Exception as exc:
            print(f"  {token:14s} skipped ({str(exc)[:40]})")
            continue
        step = max(1, len(jobs) // JOBS_PER_BOARD)            # spread over the board, not just the newest
        added = 0
        for job in jobs[::step][:JOBS_PER_BOARD]:
            try:
                detail = _get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job['id']}?questions=true")
            except Exception:
                continue
            jobs_read += 1
            for row in _questions_of(detail):
                key = row["label"].lower()[:140]
                if key not in seen:
                    seen[key] = {**row, "boards": [token]}
                    added += 1
                elif token not in seen[key].setdefault("boards", []):
                    seen[key]["boards"].append(token)
            time.sleep(0.15)
        print(f"  {token:14s} {len(jobs):4d} jobs, +{added} new questions")
    rows = sorted(seen.values(), key=lambda r: r["label"].lower())
    CORPUS.write_text(json.dumps({"_about": "Application questions read from public Greenhouse job-board question lists "
                                  "(scripts/question_coverage.py harvest). Question text, type and option labels only.",
                                  "questions": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{jobs_read} jobs read; corpus now {len(rows)} distinct questions -> {CORPUS.relative_to(ROOT)}")


def classify_all(rows: list[dict], profile=None):
    """(row, family, answer) for every question, using the agent's own answering code.

    answered = the profile or saved answers give it; policy = an acknowledgement or attestation the owner's standing
    rules decide; self_id = who the owner is, only ever the owner's own answer; essay = the writer's job; gap = none
    of those: a wording the agent does not know yet."""
    sys.path.insert(0, str(ROOT))
    from types import SimpleNamespace
    import config
    import concept_matcher as cm
    import page_agent
    import safety
    profile = profile or config.get_user_profile()
    try:
        from resume_parser import parse_resume
        resume_text = parse_resume(config.RESUME_PATH).raw_text
    except Exception:
        resume_text = ""
    job = SimpleNamespace(title="", company="X", url="", raw_text="")
    agent = page_agent.PageAgent(SimpleNamespace(values=None), None,
                                 SimpleNamespace(auto_submit=False, ats_email="", form_answer_mode="profile"),
                                 profile, SimpleNamespace(raw_text=resume_text), job)
    out = []
    for row in rows:
        label, ftype, options = row["label"], row["type"], row.get("options") or []
        role = {"multi_value_single_select": "combobox", "multi_value_multi_select": "checkbox"}.get(ftype, "textbox")
        control = page_agent.Control(ref="x", role=role, name=label, options=list(options))
        try:
            value, _source = agent.known_answer(control)
        except Exception:
            value = ""
        if value:
            out.append((row, "answered", value))
        elif cm.is_self_identification(label):
            out.append((row, "self_id", ""))
        elif safety.is_attestation(label) or POLICY.search(label):
            out.append((row, "policy", ""))
        elif ftype == "textarea" or (ftype == "input_text" and ESSAY.search(label)):
            out.append((row, "essay", ""))
        else:
            out.append((row, "gap", ""))
    return out


def score(show: int = 60) -> None:
    rows = json.loads(CORPUS.read_text(encoding="utf-8"))["questions"]
    results = classify_all(rows)
    families: dict[str, int] = {}
    for _row, family, _value in results:
        families[family] = families.get(family, 0) + 1
    boards = {b for r in rows for b in r.get("boards", [])}
    print(f"{len(rows)} distinct questions from {len(boards)} boards")
    for family, count in sorted(families.items(), key=lambda kv: -kv[1]):
        print(f"  {family:9s} {count:4d}  {100 * count // len(rows)}%")
    gaps = [r for r, family, _v in results if family == "gap"]
    print("\nreal gaps (wordings the agent does not know), most widely asked first:")
    for r in sorted(gaps, key=lambda r: (-len(r.get("boards", [])), r["label"]))[:show]:
        line = f"  [{len(r.get('boards', []))}] {'req' if r['required'] else 'opt'} {r['type'][:14]:14s} {r['label'][:110]}"
        print(line.encode("ascii", "replace").decode())


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "score"
    {"harvest": harvest, "score": score}.get(command, score)()
