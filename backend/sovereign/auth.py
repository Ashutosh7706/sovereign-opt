"""
Authentication compatibility layer for Sovereign Optimizer.

Authentication is intentionally disabled in the demo/no-login build.
The class keeps the old Auth interface so existing imports/callers do not
crash while the application treats every request as an anonymous supervisor.
"""

from __future__ import annotations

from typing import Any


class AuthError(Exception):
    """Kept for backward compatibility with existing imports."""
    pass


ANONYMOUS_USER = {
    "username": "guest",
    "role": "supervisor",
    "csrf": "",
    "disabled": False,
}


class Auth:
    """
    No-login compatibility implementation.

    This class does not create users, store PINs, create sessions, or
    authenticate credentials. It exists only so older code that imports
    sovereign.auth.Auth continues to start cleanly.
    """

    def __init__(self, store: Any = None, session_seconds: int = 0):
        self.store = store
        self.session_seconds = session_seconds

    def bootstrap(self, data_dir: Any = None):
        # Authentication is disabled: no admin/bootstrap PIN is created.
        return None

    def login(self, username: str = "", pin: str = ""):
        # Compatibility behavior: always return the anonymous demo identity.
        return None, ANONYMOUS_USER.copy()

    def logout(self, token: str | None = None):
        # No session exists in the no-login build.
        return None

    def session(self, token: str | None = None):
        # Every request is treated as the anonymous supervisor.
        return ANONYMOUS_USER.copy()

    def list_users(self):
        return [ANONYMOUS_USER.copy()]

    def create_user(self, username: str, pin: str, role: str = "operator"):
        # User management is disabled because authentication is disabled.
        return {
            "username": username,
            "role": role,
            "disabled": False,
            "authentication_disabled": True,
        }

    def set_disabled(self, username: str, disabled: bool):
        return None

    def set_pin(self, username: str, pin: str):
        # PIN management is intentionally a no-op.
        return None
