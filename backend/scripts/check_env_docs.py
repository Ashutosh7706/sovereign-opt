"""Fail if the env vars the code reads and the README configuration table drift apart (audit #89/#109).

    python backend/scripts/check_env_docs.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from sovereign.config import ENV  # noqa: E402

code_vars = set(ENV)
# every os.environ lookup in the code must be declared in config.ENV
for p in (ROOT / "backend" / "sovereign").rglob("*.py"):
    for v in re.findall(r"environ(?:\.get)?\(?\[?[\"']((?:SOVEREIGN|GUROBI)_[A-Z_]+)", p.read_text(encoding="utf-8")):
        if v not in code_vars:
            print(f"UNDECLARED: {v} read in {p.relative_to(ROOT)} but missing from sovereign/config.py ENV")
            code_vars.add(v)
readme = (ROOT / "README.md").read_text(encoding="utf-8")
doc_vars = set(re.findall(r"^\| `((?:SOVEREIGN|GUROBI)_[A-Z_]+)`", readme, re.M))
missing, stale = sorted(code_vars - doc_vars), sorted(doc_vars - code_vars)
for v in missing:
    print(f"MISSING FROM README: {v}")
for v in stale:
    print(f"STALE IN README: {v}")
print("env docs in sync" if not (missing or stale) else "env docs OUT OF SYNC")
sys.exit(1 if (missing or stale) else 0)
