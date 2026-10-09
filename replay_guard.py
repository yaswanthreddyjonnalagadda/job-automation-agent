"""Replay guard: what a code change does to every page the agent has already met.

A fix for one portal kept breaking another: tonight's grouping change for Paylocity turned Federal Recovery
Service's "How did you hear about us?" options into questions of their own, and a tie-break for Lucid read "What is
your current job title?" as the current-job tick box (29 September). The 13-minute test suite was too slow to run on
every change, so nothing showed it before a live application did.

Every page the agent reads is already saved (output/<job>/pages/, output/<job>/account/, runs/<stop>/
axtree_dump.json). This reads all of them twice -- with the code as last committed and with the code being committed,
the same profile and saved answers both times -- so every difference is the code change's own: a question read
differently, choices grouped differently, another concept, another answer, another option chosen. The pre-commit
hook runs it; a difference blocks the commit until each one is confirmed a correction:

    python replay_guard.py                 last commit against what is staged (the hook)
    REPLAY_OK=1 git commit ...             after reading the differences: every one is a correction

Nothing is sent anywhere and nothing personal is committed: the pages, the profile and the report stay on this
machine (the report is data/_replay_report.txt), and the copies made to read with are deleted afterwards.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "data" / "_replay_report.txt"
# What can change how a page is read or answered: the code, and the reference data it reads.
WATCHED = (".py", ".json")


def saved_pages() -> list[Path]:
    return sorted(ROOT.glob("output/*/pages/**/page_*.txt")) + sorted(ROOT.glob("output/*/account/*.txt")) \
        + sorted(ROOT.glob("runs/*/axtree_dump.json"))


def page_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".json":
        try:
            return json.loads(text).get("aria_snapshot", "") or ""
        except ValueError:
            return ""
    return text


# ---------------------------------------------------------------------------------------------------------------
# Reading every page with one version of the code (run in a process of its own, with that version first on the path)
# ---------------------------------------------------------------------------------------------------------------
def read_all(tree: Path) -> dict:
    sys.path[:] = [str(tree)] + [p for p in sys.path if p and Path(p).resolve() != ROOT]
    import config
    import concept_matcher
    import page_agent
    from browser_automation import JobApplicationAssistant
    best = JobApplicationAssistant._best_option
    try:
        profile = config.get_user_profile()
    except Exception:
        profile = config.UserProfile()

    readings, seen = {}, {}
    for path in saved_pages():
        text = page_text(path)
        digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
        folder = path.relative_to(ROOT).parts[1]
        if digest in seen:                       # the same page saved again: read once
            readings[str(path.relative_to(ROOT))] = {"folder": folder, "same_as": seen[digest]}
            continue
        seen[digest] = str(path.relative_to(ROOT))
        job = SimpleNamespace(title="", company=folder.split("_")[0], url="", raw_text="")
        agent = page_agent.PageAgent(SimpleNamespace(values=None), None,
                                     SimpleNamespace(auto_submit=False, ats_email="", form_answer_mode="profile"),
                                     profile, SimpleNamespace(raw_text=""), job)
        entries = []
        try:
            controls = page_agent.parse_snapshot(text)
        except Exception as exc:
            readings[str(path.relative_to(ROOT))] = {"folder": folder, "error": f"{type(exc).__name__}: {exc}"}
            continue
        groups = defaultdict(list)
        for c in controls:
            if c.role in ("radio", "checkbox") and c.group:
                groups[(c.role, c.group)].append(c)
        done = set()
        for c in controls:
            if c.role not in page_agent.ANSWER_ROLES:
                continue
            grouped = c.role in ("radio", "checkbox") and c.group
            if grouped:
                if (c.role, c.group) in done:
                    continue
                done.add((c.role, c.group))
            choices = [m.name for m in groups[(c.role, c.group)]] if grouped else list(c.options)
            try:
                concept = concept_matcher.confirm_concept(
                    concept_matcher.match_concept(c.question, c.container, c.context, c.name), c.options)
                value, source = agent.known_answer(c)
                pick = best(choices, [value]) if (choices and value) else None
                entries.append({"kind": c.role, "question": c.question[:200], "choices": choices[:40],
                                "concept": concept or "", "answer": value[:200], "source": source,
                                "chose": choices[pick] if pick is not None else ""})
            except Exception as exc:
                entries.append({"kind": c.role, "question": c.question[:200], "error": f"{type(exc).__name__}"})
        readings[str(path.relative_to(ROOT))] = {"folder": folder, "entries": entries}
    return readings


# ---------------------------------------------------------------------------------------------------------------
# Comparing
# ---------------------------------------------------------------------------------------------------------------
def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace").stdout


def export(where: Path, staged: bool, base: str = "HEAD") -> Path:
    tree = where / ("staged" if staged else "committed")
    tree.mkdir(parents=True)
    if staged:
        subprocess.run(["git", "-C", str(ROOT), "checkout-index", "-a", f"--prefix={tree.as_posix()}/"], check=True)
    else:
        archive = where / "committed.zip"
        subprocess.run(["git", "-C", str(ROOT), "archive", "--format=zip", "-o", str(archive), base], check=True)
        with zipfile.ZipFile(archive) as z:
            z.extractall(tree)
    # The owner's profile and saved answers, the same for both readings (config finds them beside the code).
    (tree / "data").mkdir(exist_ok=True)
    for source in (ROOT / "data").glob("*.json"):
        shutil.copy2(source, tree / "data" / source.name)
    return tree


def _describe(entry: dict) -> str:
    return (f"concept {entry.get('concept') or '-'}, answer {entry.get('answer')!r}"
            + (f" -> chose {entry.get('chose')!r}" if entry.get("chose") else "")
            + (f", {len(entry.get('choices') or [])} choices" if entry.get("choices") else ""))


def compare(before: dict, after: dict) -> list[str]:
    import safety
    changes = defaultdict(set)        # what changed -> the jobs it changed on
    for page in sorted(set(before) | set(after)):
        b, a = before.get(page, {}), after.get(page, {})
        if "same_as" in a or "same_as" in b:
            continue
        folder = a.get("folder") or b.get("folder") or page
        if "error" in a and "error" not in b:
            changes[f"the page can no longer be read: {a['error']}"].add(folder)
            continue
        old = {(e["kind"], e["question"]): e for e in b.get("entries", [])}
        new = {(e["kind"], e["question"]): e for e in a.get("entries", [])}
        for key in sorted(set(old) | set(new)):
            kind, question = key
            if key not in new:
                changes[f"no longer read: {kind} {question[:90]!r}"].add(folder)
            elif key not in old:
                changes[f"now read: {kind} {question[:90]!r} ({_describe(new[key])})"].add(folder)
            elif old[key] != new[key]:
                changes[f"{kind} {question[:90]!r}\n      was: {_describe(old[key])}\n      now: "
                        f"{_describe(new[key])}"].add(folder)
    lines = []
    for change, folders in sorted(changes.items(), key=lambda item: (-len(item[1]), item[0])):
        jobs = ", ".join(sorted(folders)[:4]) + (f" and {len(folders) - 4} more" if len(folders) > 4 else "")
        lines.append(safety.redact(f"- {change}\n      on: {jobs}"))
    return lines


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--read":
        json.dump(read_all(Path(sys.argv[2])), sys.stdout)
        return 0
    # --base REV compares another commit with what is staged ("what has changed since the Workday run worked?").
    base = sys.argv[sys.argv.index("--base") + 1] if "--base" in sys.argv[:-1] else "HEAD"
    staged = [name for name in _git("diff", "--cached", "--name-only").splitlines() if name.endswith(WATCHED)]
    if not staged and base == "HEAD":
        return 0
    pages = saved_pages()
    if not pages:
        print("replay guard: no saved pages on this machine -- nothing to replay")
        return 0
    if not _git("rev-parse", "--verify", "-q", base).strip():
        return 0                                   # the first commit has nothing to compare with
    with tempfile.TemporaryDirectory(prefix="replay_") as where:
        where = Path(where)
        trees = [export(where, staged=False, base=base), export(where, staged=True)]
        runs = [subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--read", str(tree)], cwd=str(ROOT),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                 errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                for tree in trees]
        outputs = [run.communicate() for run in runs]
    for (out, err), name in zip(outputs, ("last commit", "staged")):
        if not out.strip():
            print(f"replay guard: could not read the pages with the {name} code:\n{err[-1500:]}", file=sys.stderr)
            return 1
    before, after = (json.loads(out) for out, _err in outputs)
    lines = compare(before, after)
    distinct = sum(1 for r in after.values() if "same_as" not in r)
    if not lines:
        print(f"replay guard: {len(pages)} saved pages ({distinct} distinct) read the same before and after")
        return 0
    header = (f"replay guard: this change alters how {len(lines)} question(s) on the saved pages are read or "
              f"answered ({len(pages)} pages, {distinct} distinct):")
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(header + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    print(header)
    print("\n".join(lines[:60]))
    if len(lines) > 60:
        print(f"... and {len(lines) - 60} more in {REPORT}")
    if os.environ.get("REPLAY_OK") == "1":
        print("REPLAY_OK=1: confirmed as corrections -- committing")
        return 0
    print(f"\nBlocked. If every difference above is a correction: REPLAY_OK=1 git commit ...  (report: {REPORT})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
