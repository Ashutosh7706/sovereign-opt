"""Immutable, hash-chained audit log (Sec. 6 step 4; audit #26, #35, #36).

Each entry stores hash = sha256(prev_hash + canonical(entry)). Editing or deleting any row
breaks every later hash, which `verify()` reports with the first bad sequence number.

External anchoring (#36): `anchor(dir)` appends the current chain tip (seq, hash) to a file
in a directory that should live OFF this machine or on write-once media (USB/WORM share,
printed shift report). A local admin who regenerates the whole chain cannot also change
those anchors, so `verify()` flags the rewrite.

Entries are rows in SQLite (WAL, same transaction as the state change they describe).
`export()` writes a JSONL archive for off-box retention; the ledger itself is never pruned.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .store import Store

GENESIS = "0" * 64


def _canon(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _hash(prev: str, body: dict) -> str:
    return hashlib.sha256((prev + _canon(body)).encode()).hexdigest()


class AuditLog:
    def __init__(self, store: Store, anchor_dir: str | Path | None = None):
        self.store = store
        self.anchor_dir = Path(anchor_dir) if anchor_dir else None

    def append(self, event: str, actor: str, payload: dict) -> dict:
        with self.store.tx() as c:
            row = c.execute("SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            prev, n = (row["hash"], row["seq"]) if row else (GENESIS, 0)
            body = {"seq": n + 1, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event,
                    "actor": actor, "payload": json.loads(_canon(payload)), "prev": prev}
            h = _hash(prev, body)
            c.execute("INSERT INTO audit(seq, ts, event, actor, payload, prev, hash) VALUES(?,?,?,?,?,?,?)",
                      (body["seq"], body["ts"], event, actor, _canon(body["payload"]), prev, h))
            body["hash"] = h
            return body

    @staticmethod
    def _row(r) -> dict:
        return {"seq": r["seq"], "ts": r["ts"], "event": r["event"], "actor": r["actor"],
                "payload": json.loads(r["payload"]), "prev": r["prev"], "hash": r["hash"]}

    def entries(self, limit: int | None = None) -> list[dict]:
        if limit:
            rows = self.store.read("SELECT * FROM (SELECT * FROM audit ORDER BY seq DESC LIMIT ?) ORDER BY seq",
                                   (limit,))
        else:
            rows = self.store.read("SELECT * FROM audit ORDER BY seq")
        return [self._row(r) for r in rows]

    def tip(self) -> tuple[int, str]:
        r = self.store.read("SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1")
        return (r[0]["seq"], r[0]["hash"]) if r else (0, GENESIS)

    def verify(self) -> dict:
        prev, n = GENESIS, 0
        by_seq = {}
        for r in self.store.read("SELECT * FROM audit ORDER BY seq"):
            n += 1
            try:
                e = self._row(r)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {"ok": False, "entries": n - 1, "bad_seq": r["seq"], "reason": "unparseable payload"}
            if e["seq"] != n:
                return {"ok": False, "entries": n - 1, "bad_seq": e["seq"], "reason": "sequence gap (row deleted?)"}
            h = e.pop("hash")
            if e["prev"] != prev:
                return {"ok": False, "entries": n - 1, "bad_seq": e["seq"], "reason": "chain link broken"}
            if _hash(prev, e) != h:
                return {"ok": False, "entries": n - 1, "bad_seq": e["seq"], "reason": "content hash mismatch"}
            by_seq[e["seq"]] = h
            prev = h
        # anchors: internal table + external write-once files
        anchors = [(a["seq"], a["hash"], "local anchor table") for a in
                   self.store.read("SELECT seq, hash FROM anchors")]
        if self.anchor_dir and self.anchor_dir.exists():
            for f in sorted(self.anchor_dir.glob("audit-anchors*.jsonl")):
                for line in f.read_text().splitlines():
                    if line.strip():
                        a = json.loads(line)
                        anchors.append((a["seq"], a["hash"], f"external {f.name}"))
        for seq, h, where in anchors:
            if by_seq.get(seq) != h:
                return {"ok": False, "entries": n, "bad_seq": seq,
                        "reason": f"chain does not match anchor from {where} - log was rewritten"}
        return {"ok": True, "entries": n, "head": prev, "anchors_checked": len(anchors)}

    def anchor(self, destination: str | Path | None = None, actor: str = "system") -> dict:
        dest = Path(destination) if destination else self.anchor_dir
        if dest is None:
            raise ValueError("no anchor destination configured (SOVEREIGN_ANCHOR_DIR)")
        dest.mkdir(parents=True, exist_ok=True)
        seq, h = self.tip()
        rec = {"seq": seq, "hash": h, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        with (dest / "audit-anchors.jsonl").open("a", encoding="utf-8") as f:
            f.write(_canon(rec) + "\n")
        with self.store.tx() as c:
            c.execute("INSERT INTO anchors(ts, seq, hash, destination) VALUES(?,?,?,?)",
                      (rec["ts"], seq, h, str(dest)))
            self.append("audit.anchored", actor, {"seq": seq, "hash": h, "destination": str(dest)})
        return rec

    def export(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for e in self.entries():
                f.write(_canon(e) + "\n")
        return path
