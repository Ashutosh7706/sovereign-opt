"""SQLite persistence (audit #26, #31, #33, #34, #38).

* One database file, WAL mode, busy timeout, an in-process write lock.
* `tx()` is re-entrant: a state change and its audit entry are written in ONE transaction,
  so a failed write (disk full, locked file) leaves neither behind - fail closed.
* Schema migrations are plain, numbered, idempotent SQL steps recorded in `schema_version`
  (a few tables do not justify Alembic; switch when a server database arrives).
* The file is created owner-only (0600) where the OS supports POSIX permissions.
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
from pathlib import Path

SCHEMA_VERSION = 3
MIGRATIONS: dict[int, list[str]] = {
    1: [
        """CREATE TABLE IF NOT EXISTS audit (
             seq INTEGER PRIMARY KEY, ts TEXT NOT NULL, event TEXT NOT NULL, actor TEXT NOT NULL,
             payload TEXT NOT NULL, prev TEXT NOT NULL, hash TEXT NOT NULL)""",
        "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, created TEXT, body TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS constraints (id TEXT PRIMARY KEY, position INTEGER, body TEXT NOT NULL)",
        """CREATE TABLE IF NOT EXISTS bundles (id TEXT PRIMARY KEY, created TEXT, schema_version INTEGER,
             body TEXT NOT NULL)""",
        "CREATE TABLE IF NOT EXISTS models (fingerprint TEXT PRIMARY KEY, body TEXT NOT NULL)",
    ],
    2: [
        """CREATE TABLE IF NOT EXISTS anchors (
             id INTEGER PRIMARY KEY, ts TEXT NOT NULL, seq INTEGER NOT NULL, hash TEXT NOT NULL,
             destination TEXT NOT NULL)""",
    ],
    3: [
        # Authentication was removed from the no-login build.
        # Drop legacy authentication tables from databases created by older versions.
        "DROP TABLE IF EXISTS sessions",
        "DROP TABLE IF EXISTS users",
    ],
}


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()
        self._depth = 0
        if new:
            try:
                os.chmod(self.path, 0o600)
            except OSError:  # pragma: no cover - e.g. some Windows filesystems
                pass
        self._migrate()

    # -------------------------------------------------------------- transactions
    @contextlib.contextmanager
    def tx(self):
        with self._lock:
            outer = self._depth == 0
            if outer:
                self.conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self.conn
                self._depth -= 1
                if outer:
                    self.conn.execute("COMMIT")
            except BaseException:
                self._depth -= 1
                if outer:
                    self.conn.execute("ROLLBACK")
                raise

    def read(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    def _migrate(self):
        with self.tx() as c:
            c.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied TEXT)")
            done = {r[0] for r in c.execute("SELECT version FROM schema_version")}
            for v in sorted(MIGRATIONS):
                if v in done:
                    continue
                for stmt in MIGRATIONS[v]:
                    c.execute(stmt)
                c.execute("INSERT INTO schema_version VALUES (?, datetime('now'))", (v,))

    def schema_version(self) -> int:
        return int(self.read("SELECT MAX(version) FROM schema_version")[0][0])

    # -------------------------------------------------------------- key/value
    def get(self, key: str, default=None):
        rows = self.read("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(rows[0][0]) if rows else default

    def put(self, key: str, value) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, json.dumps(value, default=str)))

    # -------------------------------------------------------------- maintenance
    def backup(self, dest: str | Path) -> Path:
        """Consistent online backup (SQLite backup API) - safe while the server runs."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            out = sqlite3.connect(str(dest))
            with out:
                self.conn.backup(out)
            out.close()
        try:
            os.chmod(dest, 0o600)
        except OSError:  # pragma: no cover
            pass
        return dest

    def integrity_ok(self) -> bool:
        return self.read("PRAGMA quick_check")[0][0] == "ok"

    def close(self):
        with self._lock:
            self.conn.close()
