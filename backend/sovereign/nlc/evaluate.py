"""Labeled evaluation of the NL compiler: accuracy, red-team invariants, confidence calibration
(audit #74/#94 benchmark harness, #95 red-team, #97 calibration, #101 compound sentences).

    python -m sovereign.nlc.evaluate                       # offline rules (default)
    python -m sovereign.nlc.evaluate sovereign-local-llm   # e.g. benchmark an on-prem 8B vs 34B model
    python -m sovereign.nlc.evaluate --write ../docs/NL_CALIBRATION.md
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from ..audit import AuditLog
from ..store import Store
from .gate import ConstraintGate

EVAL_FILE = Path(__file__).resolve().parents[2] / "tests" / "nlc_eval.jsonl"
BUCKETS = [(0.0, 0.5), (0.5, 0.8), (0.8, 0.9), (0.9, 1.01)]


def load_eval(path: Path = EVAL_FILE) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _matches(ir: dict | None, expect: list) -> bool:
    if ir is None:
        return False
    kind, entity, sense, value, unit = expect
    if ir["kind"] != kind or ir["entity"] != entity:
        return False
    if kind == "unit_status":
        return ir.get("status") == value
    return ir.get("sense") == sense and abs((ir.get("value") or 0) - value) <= 1e-9 * (1 + abs(value)) \
        and ir.get("unit") == unit


def evaluate(mode: str = "sovereign-rules", items: list[dict] | None = None) -> dict:
    items = items or load_eval()
    with tempfile.TemporaryDirectory() as d:
        store = Store(Path(d) / "eval.db")
        gate = ConstraintGate(AuditLog(store), cloud_allowed=True)
        rows = []
        t0 = time.perf_counter()
        for it in items:
            p = gate.propose(it["text"], "evaluator", mode, role="operator")
            correct = _matches(p.get("ir"), it["expect"]) if it.get("expect") else None
            rows.append({"text": it["text"], "status": p["status"], "confidence": p.get("confidence", 0.0),
                         "correct": correct, "expect": it.get("expect"), "ir": p.get("ir"),
                         "must_signoff": it.get("must_signoff", False), "reject": it.get("reject", False),
                         "tags": it.get("tags", []), "parser": p.get("parser")})
        store.close()
    seconds = time.perf_counter() - t0
    labelled = [r for r in rows if r["correct"] is not None]
    auto = [r for r in rows if r["status"] == "auto_accepted"]
    violations = []
    for r in rows:
        if r["status"] == "auto_accepted" and (r["must_signoff"] or r["reject"] or r["correct"] is False):
            violations.append(f"AUTO-ACCEPTED but should not be: {r['text']!r}")
        if r["reject"] and r["status"] not in ("unparsed", "blocked"):
            violations.append(f"should be rejected, got {r['status']}: {r['text']!r}")
        if r["must_signoff"] and r["status"] == "auto_accepted":
            pass  # already reported
    calib = []
    for lo, hi in BUCKETS:
        b = [r for r in labelled if lo <= r["confidence"] < hi]
        calib.append({"bucket": f"{lo:.1f}-{min(hi, 1.0):.1f}", "n": len(b),
                      "accuracy": (sum(r["correct"] for r in b) / len(b)) if b else None})
    return {"mode": mode, "n": len(rows), "seconds": seconds,
            "parse_accuracy": sum(r["correct"] for r in labelled) / len(labelled) if labelled else None,
            "auto_accepted": len(auto),
            "auto_accept_precision": (sum(1 for r in auto if r["correct"]) / len(auto)) if auto else None,
            "violations": violations, "calibration": calib, "rows": rows}


def to_markdown(res: dict) -> str:
    out = [f"# NL compiler evaluation - mode `{res['mode']}`", "",
           f"Items: **{res['n']}** (labelled set `backend/tests/nlc_eval.jsonl`, incl. red-team phrasings) - "
           f"{res['seconds']:.1f} s", "",
           f"* Parse accuracy on labelled items: **{res['parse_accuracy']:.0%}**",
           f"* Auto-accepted (no human): **{res['auto_accepted']}**, of which correct: "
           f"**{(res['auto_accept_precision'] or 0):.0%}**",
           f"* Red-team / policy violations: **{len(res['violations'])}**", ""]
    out += [f"  * {v}" for v in res["violations"]]
    out += ["", "## Confidence calibration", "",
            "| confidence | items | parse accuracy |", "|---|---|---|"]
    for c in res["calibration"]:
        acc = "-" if c["accuracy"] is None else f"{c['accuracy']:.0%}"
        out.append(f"| {c['bucket']} | {c['n']} | {acc} |")
    out += ["", "Reading: a bucket's accuracy should be at least its lower bound. The score is a *rule-based",
            "risk score*, not a probability - high-risk wording is pushed down so a human sees it.", "",
            "## Every item", "", "| status | conf | correct | text |", "|---|---|---|---|"]
    for r in res["rows"]:
        ok = "" if r["correct"] is None else ("yes" if r["correct"] else "**NO**")
        out.append(f"| {r['status']} | {r['confidence']:.2f} | {ok} | {r['text'].replace('|', '/')} |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    args = sys.argv[1:]
    write = None
    if "--write" in args:
        i = args.index("--write")
        write = Path(args[i + 1])
        del args[i:i + 2]
    res = evaluate(args[0] if args else "sovereign-rules")
    md = to_markdown(res)
    if write:
        write.parent.mkdir(parents=True, exist_ok=True)
        write.write_text(md, encoding="utf-8")
    sys.stdout.buffer.write(md.encode("utf-8"))
    raise SystemExit(1 if res["violations"] else 0)
