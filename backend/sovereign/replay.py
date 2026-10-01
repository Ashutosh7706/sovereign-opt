"""Replay bundles for post-incident forensic replay (Sec. 14.1; audit #34, #73).

Bundle = input fingerprint (SHA-256 of the canonical model), solver version, full config
(incl. deterministic flag), the complete iteration log and a SHA-256 of the solution
vector. `replay()` re-solves from the stored model and checks the digest bit-for-bit.

Bundles carry `schema_version`; `_upgrade()` migrates older layouts on read.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import fields

from .engine import SolverConfig, solve
from .model import Model
from .store import Store

BUNDLE_SCHEMA = 2


def _jsonable(o):
    if isinstance(o, float) and (o != o or o in (float("inf"), float("-inf"))):
        return None
    if hasattr(o, "item"):
        return o.item()
    return o


def _upgrade(b: dict) -> dict:
    """v1 (file-based, no schema_version) -> v2: add schema_version + algorithm fields."""
    v = b.get("schema_version", 1)
    if v < 2:
        b.setdefault("algorithm", "mehrotra")
        b["schema_version"] = 2
    return b


class ReplayStore:
    def __init__(self, store: Store):
        self.store = store

    def record(self, model: Model, result: dict, label: str = "") -> dict:
        fp = model.fingerprint()
        bid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        bundle = {
            "schema_version": BUNDLE_SCHEMA, "id": bid, "label": label,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "model_name": model.name, "model_sha256": fp, "solver_version": result["solver_version"],
            "config": result["config"], "deterministic": result["config"]["deterministic"], "seed": 0,
            "status": result["status"], "objective": _jsonable(result["objective"]),
            "x_sha256": result["x_sha256"], "iterations": result["iters"], "nodes": result["nodes"],
            "algorithm": result.get("algorithm", "mehrotra"),
            "iteration_log": [{k: _jsonable(v) for k, v in e.items()} for e in result["log"]],
        }
        with self.store.tx() as c:
            c.execute("INSERT OR IGNORE INTO models(fingerprint, body) VALUES(?, ?)",
                      (fp, json.dumps(model.to_dict())))
            c.execute("INSERT INTO bundles(id, created, schema_version, body) VALUES(?,?,?,?)",
                      (bid, bundle["created"], BUNDLE_SCHEMA, json.dumps(bundle, default=str)))
        return bundle

    def list(self, limit: int = 50) -> list[dict]:
        out = []
        for r in self.store.read("SELECT body FROM bundles ORDER BY id DESC LIMIT ?", (limit,)):
            b = _upgrade(json.loads(r["body"]))
            b.pop("iteration_log", None)
            out.append(b)
        return out

    def get(self, bid: str) -> dict:
        rows = self.store.read("SELECT body FROM bundles WHERE id=?", (bid,))
        if not rows:
            raise FileNotFoundError(bid)
        return _upgrade(json.loads(rows[0]["body"]))

    def model(self, fingerprint: str) -> Model:
        rows = self.store.read("SELECT body FROM models WHERE fingerprint=?", (fingerprint,))
        if not rows:
            raise FileNotFoundError(fingerprint)
        return Model.from_dict(json.loads(rows[0]["body"]))

    def replay(self, bid: str) -> dict:
        b = self.get(bid)
        model = self.model(b["model_sha256"])
        if model.fingerprint() != b["model_sha256"]:
            return {"ok": False, "bundle": bid, "reason": "stored model does not match its fingerprint (tampered?)"}
        known = {f.name for f in fields(SolverConfig)}
        cfg = SolverConfig(**{k: v for k, v in b["config"].items() if k in known})
        r = solve(model, cfg)
        same = r["x_sha256"] == b["x_sha256"]
        return {"ok": same, "bundle": bid, "deterministic": cfg.deterministic,
                "original": {"x_sha256": b["x_sha256"], "objective": b["objective"], "iterations": b["iterations"]},
                "replayed": {"x_sha256": r["x_sha256"], "objective": r["objective"], "iterations": r["iters"]},
                "reason": "bit-exact match" if same else "solution digest differs - replay FAILED"}
