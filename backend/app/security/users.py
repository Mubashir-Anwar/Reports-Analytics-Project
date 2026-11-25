"""/server user accounts (PBKDF2 hashes), roles and signed session cookies."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from backend.app.constants import LEGACY_PASSWORD_ITERATIONS, PASSWORD_ITERATIONS, USER_RIGHTS, USER_ROLES


# ----------------------------------------------------------------- server users and login sessions
def _hash_password(password: str, salt: str | None = None, iterations: int = PASSWORD_ITERATIONS) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return salt, digest.hex()


def set_user_password(user: dict[str, Any], password: str) -> None:
    """Store a new salted hash (current iteration count) on the user record."""
    user["salt"], user["hash"] = _hash_password(password)
    user["iterations"] = PASSWORD_ITERATIONS


def password_needs_upgrade(user: dict[str, Any]) -> bool:
    return int(user.get("iterations", LEGACY_PASSWORD_ITERATIONS)) < PASSWORD_ITERATIONS


def load_users() -> list[dict[str, Any]]:
    """Accounts from the users and user_rights tables (db/people.py), oldest first."""
    from backend.app.db.people import load_users as load  # noqa: PLC0415
    return load()


def save_users(users: list[dict[str, Any]], *, by: str = "") -> None:
    from backend.app.db.people import save_users as save  # noqa: PLC0415
    save(users, by=by)


def find_user(username: str) -> dict[str, Any] | None:
    wanted = username.strip().casefold()
    return next((item for item in load_users() if str(item["username"]).casefold() == wanted), None)


def check_user_password(user: dict[str, Any], password: str) -> bool:
    try:
        _, digest = _hash_password(password, str(user.get("salt", "")), int(user.get("iterations", LEGACY_PASSWORD_ITERATIONS)))
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(digest, str(user.get("hash", "")))


def is_super_admin(user: dict[str, Any]) -> bool:
    """Super admins have every right. Accounts saved before rights existed: role admin = super admin."""
    if "superAdmin" in user:
        return bool(user.get("superAdmin"))
    return "rights" not in user and user.get("role") == "admin"


_LEGACY_RIGHTS = {"designer": ("designer", "query", "server", "runner"), "viewer": ("server", "runner")}


def user_rights(user: dict[str, Any]) -> set[str]:
    """The parts of the product this account may open (super admin: all of them)."""
    if is_super_admin(user):
        return set(USER_RIGHTS)
    if isinstance(user.get("rights"), list):
        return {str(item) for item in user["rights"]} & set(USER_RIGHTS)
    return set(_LEGACY_RIGHTS.get(str(user.get("role", "viewer")), ()))


def derived_role(rights: set[str], super_admin: bool = False) -> str:
    """viewer/designer/admin for folder permissions inside /server."""
    if super_admin or "admin" in rights:
        return "admin"
    return "designer" if "designer" in rights else "viewer"


def is_temporary(user: dict[str, Any]) -> bool:
    """The setup admin (python -m backend.app.setup): it switches itself off at expiresAt."""
    return bool(user.get("temporary"))


def is_expired(user: dict[str, Any]) -> bool:
    """A temporary account after its time (a broken date counts as over)."""
    if not is_temporary(user):
        return False
    try:
        return datetime.fromisoformat(str(user.get("expiresAt") or "")) <= datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return True


def is_permanent_super_admin(user: dict[str, Any]) -> bool:
    return is_super_admin(user) and not user.get("disabled") and not is_temporary(user)


def needs_first_admin() -> bool:
    """No permanent super admin yet: the setup admin still has to create one."""
    return not any(is_permanent_super_admin(item) for item in load_users())


def role_at_least(role: str, minimum: str) -> bool:
    return role in USER_ROLES and USER_ROLES.index(role) >= USER_ROLES.index(minimum)


def _session_secret() -> bytes:
    configured = os.getenv("REPORTING_SESSION_SECRET", "").strip()
    if configured:
        return configured.encode("utf-8")
    # Derived from the admin token: rotating the admin token signs every user out.
    return hmac.new(os.getenv("REPORTING_ADMIN_TOKEN", "").encode("utf-8"), b"server-session-v1", hashlib.sha256).digest()


def session_lifetime_seconds() -> int:
    try:
        hours = float(os.getenv("REPORTING_SESSION_HOURS", "8"))
    except ValueError:
        hours = 8
    return int(max(0.25, min(hours, 24 * 30)) * 3600)


def make_session_cookie(username: str, version: int) -> str:
    body = {"u": username, "v": version, "exp": int(time.time()) + session_lifetime_seconds(), "n": secrets.token_hex(8)}
    encoded = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(_session_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def read_session_cookie(value: str) -> dict[str, Any] | None:
    if not value or "." not in value:
        return None
    encoded, signature = value.rsplit(".", 1)
    expected = hmac.new(_session_secret(), encoded.encode("ascii", "ignore"), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(signature, expected):
        return None
    try:
        body = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except (ValueError, TypeError):
        return None
    if not isinstance(body, dict) or int(body.get("exp", 0)) <= time.time():
        return None
    return body
