"""Every project module a committed file imports is itself committed.

28 September: web_ui.py and browser_automation.py were committed importing visible_desktop.py, which was not
committed, so the code on GitHub could not start. Run by .githooks/pre-commit on what is staged; about a second.
"""
import ast
import subprocess
import sys
from pathlib import Path


def git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def main() -> int:
    root = Path(git("rev-parse", "--show-toplevel")[0])
    committed = set(git("ls-files")) | set(git("diff", "--cached", "--name-only", "--diff-filter=A"))
    deleted = set(git("diff", "--cached", "--name-only", "--diff-filter=D"))
    committed -= deleted
    local = {Path(f).stem for f in committed if f.endswith(".py") and "/" not in f}
    local |= {f.split("/")[0] for f in committed if f.endswith("/__init__.py") and f.count("/") == 1}
    on_disk = {p.stem for p in root.glob("*.py")} | {p.parent.name for p in root.glob("*/__init__.py")}
    staged = [f for f in git("diff", "--cached", "--name-only", "--diff-filter=ACM") if f.endswith(".py")]
    missing = []
    for name in staged:
        try:
            tree = ast.parse((root / name).read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            missing.append(f"{name}: does not parse ({exc.msg}, line {exc.lineno})")
            continue
        for node in ast.walk(tree):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 else []
            for mod in mods:
                top = mod.split(".")[0]
                if top in on_disk and top not in local:
                    missing.append(f"{name} imports {top}, which is not committed (git add {top}.py)")
    if missing:
        print("Not committing:\n  " + "\n  ".join(dict.fromkeys(missing)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
