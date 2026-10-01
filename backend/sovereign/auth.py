"""Operator authentication, roles and sessions (audit #21, #25, #28).

* PINs are hashed with scrypt (stdlib, memory-hard) and a per-user salt - never stored.
* Roles: operator < supervisor < admin. Safety-tagged sign-off and shadow->production
  promotion need supervisor; settings and user management need admin.
* Sessions: random 256-bit token in an HttpOnly, SameSite=Strict cookie; only its SHA-256
  is stored. Each session carries its own CSRF token (double-submit header check).
* Brute force: 5 wrong PINs lock the account for 15 minutes; every attempt is audited.
* First boot with no users creates `admin` with a random PIN written to
  data/BOOTSTRAP_ADMIN.txt (owner-only). Change it and delete the file.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from pathlib import Path

from .store import Store

ROLES = ("operator", "supervisor", "admin")
MAX_FAILED = 5
LOCK_SECONDS = 15 * 60
USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")


class AuthError(Exception):
    pass


def _hash_pin(pin: str, salt: bytes) -> str:
    return hashlib.scrypt(pin.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32).hex()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    def __init__(self, store: Store, session_ttl: float = 12 * 3600):
        self.store = store
        self.ttl = session_ttl

    # ---------------------------------------------------------------- users
    def has_users(self) -> bool:
        return bool(self.store.read("SELECT 1 FROM users LIMIT 1"))

    def create_user(self, username: str, pin: str, role: str) -> dict:
        username = username.strip().lower()
        if not USER_RE.match(username):
            raise AuthError("username: 2-32 chars, lowercase letters, digits, . _ -")
        if role not in ROLES:
            raise AuthError(f"role must be one of {ROLES}")
        if len(pin) < 6:
            raise AuthError("PIN must be at least 6 characters")
        salt = secrets.token_bytes(16)
        with self.store.tx() as c:
            if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise AuthError("user already exists")
            c.execute("INSERT INTO users(username, role, salt, pin_hash, created) VALUES(?,?,?,?,?)",
                      (username, role, salt.hex(), _hash_pin(pin, salt), time.strftime("%Y-%m-%dT%H:%M:%S%z")))
        return {"username": username, "role": role}

    def set_pin(self, username: str, pin: str) -> None:
        if len(pin) < 6:
            raise AuthError("PIN must be at least 6 characters")
        salt = secrets.token_bytes(16)
        with self.store.tx() as c:
            n = c.execute("UPDATE users SET salt=?, pin_hash=?, failed=0, locked_until=0 WHERE username=?",
                          (salt.hex(), _hash_pin(pin, salt), username)).rowcount
            c.execute("DELETE FROM sessions WHERE username=?", (username,))
        if not n:
            raise AuthError("no such user")

    def set_disabled(self, username: str, disabled: bool) -> None:
        with self.store.tx() as c:
            c.execute("UPDATE users SET disabled=? WHERE username=?", (int(disabled), username))
            if disabled:
                c.execute("DELETE FROM sessions WHERE username=?", (username,))

    def list_users(self) -> list[dict]:
        return [{"username": r["username"], "role": r["role"], "created": r["created"],
                 "disabled": bool(r["disabled"]), "locked": r["locked_until"] > time.time()}
                for r in self.store.read("SELECT * FROM users ORDER BY username")]

    def bootstrap(self, data_dir: Path) -> str | None:
        """Create the first admin if the user table is empty; returns the one-time PIN."""
        if self.has_users():
            return None
        pin = secrets.token_urlsafe(9)
        self.create_user("admin", pin, "admin")
        f = Path(data_dir) / "BOOTSTRAP_ADMIN.txt"
        f.write_text(f"username: admin\nPIN: {pin}\n\nLog in, create named users, change this PIN, "
                     f"then delete this file.\n")
        try:
            os.chmod(f, 0o600)
        except OSError:  # pragma: no cover
            pass
        return pin

    # ---------------------------------------------------------------- sessions
    def login(self, username: str, pin: str) -> tuple[str, dict]:
        username = (username or "").strip().lower()
        rows = self.store.read("SELECT * FROM users WHERE username=?", (username,))
        if not rows:
            _hash_pin(pin or "x", b"constant-time-pad")  # similar timing for unknown users
            raise AuthError("unknown user or wrong PIN")
        u = rows[0]
        if u["disabled"]:
            raise AuthError("account disabled")
        if u["locked_until"] > time.time():
            raise AuthError(f"account locked after {MAX_FAILED} failed attempts - try again later")
        ok = hmac.compare_digest(_hash_pin(pin or "", bytes.fromhex(u["salt"])), u["pin_hash"])
        if not ok:  # record the failure (committed) before refusing
            failed = u["failed"] + 1
            lock = time.time() + LOCK_SECONDS if failed >= MAX_FAILED else 0
            with self.store.tx() as c:
                c.execute("UPDATE users SET failed=?, locked_until=? WHERE username=?",
                          (0 if lock else failed, lock, username))
            raise AuthError("unknown user or wrong PIN" + (" - account now locked" if lock else ""))
        with self.store.tx() as c:
            c.execute("UPDATE users SET failed=0, locked_until=0 WHERE username=?", (username,))
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            now = time.time()
            c.execute("DELETE FROM sessions WHERE expires < ?", (now,))
            c.execute("INSERT INTO sessions(token_hash, username, csrf, created, expires) VALUES(?,?,?,?,?)",
                      (_token_hash(token), username, csrf, now, now + self.ttl))
        return token, {"username": username, "role": u["role"], "csrf": csrf}

    def session(self, token: str | None) -> dict | None:
        if not token:
            return None
        rows = self.store.read(
            "SELECT s.username, s.csrf, s.expires, u.role, u.disabled FROM sessions s "
            "JOIN users u ON u.username = s.username WHERE s.token_hash=?", (_token_hash(token),))
        if not rows or rows[0]["expires"] < time.time() or rows[0]["disabled"]:
            return None
        r = rows[0]
        return {"username": r["username"], "role": r["role"], "csrf": r["csrf"]}

    def logout(self, token: str | None) -> None:
        if token:
            with self.store.tx() as c:
                c.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))
