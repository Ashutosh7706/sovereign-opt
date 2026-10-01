"""Site-operations CLI (audit #32, #35, #36, #109, #111). Run from backend/:

    python manage.py config                         validate + print every env var (fail fast on errors)
    python manage.py backup [--dest DIR] [--keep N] consistent online SQLite backup + retention
    python manage.py verify-audit                   check the hash chain (+ external anchors)
    python manage.py anchor --dest DIR              write the chain tip to an off-box / write-once location
    python manage.py export-audit FILE              JSONL archive of the full chain
    python manage.py create-user NAME ROLE          prompts for a PIN (operator|supervisor|admin)
    python manage.py reset-pin NAME                 prompts for a new PIN, unlocks, ends sessions
    python manage.py list-users
    python manage.py purge --yes                    DELETE all platform data (database, backups, exports)
"""
from __future__ import annotations

import argparse
import getpass
import shutil
import sys
import time
from pathlib import Path

from sovereign import config
from sovereign.audit import AuditLog
from sovereign.auth import Auth, AuthError
from sovereign.store import Store


def _store(s) -> Store:
    return Store(Path(s.data_dir) / "sovereign.db")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sovereign Optimizer site operations")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("config")
    b = sub.add_parser("backup"); b.add_argument("--dest"); b.add_argument("--keep", type=int, default=30)
    sub.add_parser("verify-audit")
    a = sub.add_parser("anchor"); a.add_argument("--dest")
    e = sub.add_parser("export-audit"); e.add_argument("file")
    c = sub.add_parser("create-user"); c.add_argument("name"); c.add_argument("role")
    r = sub.add_parser("reset-pin"); r.add_argument("name")
    sub.add_parser("list-users")
    p = sub.add_parser("purge"); p.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)

    try:
        s = config.load()
    except Exception as ex:
        print(f"INVALID CONFIGURATION:\n{ex}", file=sys.stderr)
        return 2
    data = Path(s.data_dir)

    if args.cmd == "config":
        for row in config.describe(s):
            print(f"{row['env']:28s} {'*' if row['set'] else ' '} {row['value'][:40]:40s} {row['description']}")
        if s.unknown_env:
            print("\nUNKNOWN SOVEREIGN_* variables (typo?):", ", ".join(s.unknown_env))
        return 0

    if args.cmd == "purge":
        if not args.yes:
            print(f"This permanently deletes {data} (database, audit log, replay bundles, backups, exports).\n"
                  f"Export the audit log first if retention rules require it. Re-run with --yes to proceed.")
            return 1
        if data.exists():
            shutil.rmtree(data)
        print(f"purged {data}. Also delete any off-box anchors/backups per your retention policy.")
        return 0

    store = _store(s)
    audit = AuditLog(store, s.anchor_dir)
    auth = Auth(store)
    if args.cmd == "backup":
        dest = Path(args.dest) if args.dest else data / "backups"
        f = store.backup(dest / (time.strftime("sovereign-%Y%m%d-%H%M%S") + f"-{time.time_ns() % 10**6:06d}.db"))
        old = sorted(dest.glob("sovereign-*.db"))[:-args.keep] if args.keep > 0 else []
        for o in old:
            o.unlink()
        audit.append("ops.backup", "cli", {"file": str(f), "pruned": len(old)})
        print(f"backup written: {f}  (kept newest {args.keep}, pruned {len(old)})")
    elif args.cmd == "verify-audit":
        v = audit.verify()
        print(v)
        return 0 if v["ok"] else 1
    elif args.cmd == "anchor":
        rec = audit.anchor(args.dest, actor="cli")
        print(f"anchored seq {rec['seq']} hash {rec['hash']} -> {args.dest or s.anchor_dir}")
    elif args.cmd == "export-audit":
        f = audit.export(args.file)
        audit.append("audit.exported", "cli", {"file": str(f)})
        print(f"exported {f}")
    elif args.cmd in ("create-user", "reset-pin"):
        pin = getpass.getpass("PIN (6+ characters): ")
        if pin != getpass.getpass("repeat PIN: "):
            print("PINs differ", file=sys.stderr)
            return 1
        try:
            if args.cmd == "create-user":
                auth.create_user(args.name, pin, args.role)
                audit.append("auth.user_created", "cli", {"username": args.name, "role": args.role})
            else:
                auth.set_pin(args.name, pin)
                audit.append("auth.pin_reset", "cli", {"username": args.name})
        except AuthError as ex:
            print(ex, file=sys.stderr)
            return 1
        print("ok")
    elif args.cmd == "list-users":
        for u in auth.list_users():
            print(f"{u['username']:24s} {u['role']:10s} {'disabled' if u['disabled'] else 'locked' if u['locked'] else 'active'}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
