"""private_secrets.json (admin token, adapter secrets) and the optional local .env file."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from typing import Any

from backend.app.constants import PROJECT_ROOT
from backend.app.logs import LOG
from backend.app.storage.store import get_store

ENCRYPTED_FORMAT = "fernet-v1"


def _cipher():
    """Fernet (AES + HMAC) from REPORTING_SECRETS_KEY, or None when no key is set.
    The key may be a Fernet key (python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    or any passphrase of at least 32 characters."""
    raw = os.getenv("REPORTING_SECRETS_KEY", "").strip()
    if not raw:
        return None
    from cryptography.fernet import Fernet
    try:
        return Fernet(raw.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        if len(raw) < 32:
            raise RuntimeError("REPORTING_SECRETS_KEY must be a Fernet key or a passphrase of at least 32 characters") from None
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest()))


def _load_private_secrets() -> dict[str, Any]:
    raw = get_store().read_json("private_secrets.json")
    if raw is None:
        return {"admin_token": "", "mongo_connections": {}}
    if not isinstance(raw, dict):
        raise ValueError("The root of private_secrets.json must be a JSON object")
    if raw.get("format") == ENCRYPTED_FORMAT:
        cipher = _cipher()
        if cipher is None:
            raise RuntimeError("The private secrets are encrypted: set REPORTING_SECRETS_KEY (the same key as when they were saved)")
        from cryptography.fernet import InvalidToken
        try:
            raw = json.loads(cipher.decrypt(str(raw.get("data", "")).encode("ascii")))
        except InvalidToken:
            raise RuntimeError("REPORTING_SECRETS_KEY does not open the private secrets (wrong key?)") from None
    elif _cipher() is not None:
        _save_private_secrets(raw)  # first start with a key: encrypt what was stored in plain text
        LOG.info("The private secrets are now encrypted with REPORTING_SECRETS_KEY")
    connections = raw.get("mongo_connections", {})
    if not isinstance(connections, dict):
        raise ValueError("mongo_connections in private_secrets.json must be a JSON object")
    return {
        "admin_token": str(raw.get("admin_token", "")).strip(),
        "mongo_connections": connections,
    }


def _save_private_secrets(payload: dict[str, Any]) -> None:
    """Admin token and adapter URLs/passwords. Encrypted when REPORTING_SECRETS_KEY is set (recommended: then a
    copy of the data folder or of the storage database alone does not reveal any database password).
    files backend: mode 0600 on Unix (Windows inherits the current user's ACL); PostgreSQL: a private row."""
    cipher = _cipher()
    if cipher is not None:
        token = cipher.encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
        payload = {"format": ENCRYPTED_FORMAT, "data": token}
    get_store().write_json("private_secrets.json", payload, private=True)


def ensure_admin_token() -> str:
    """Use a configured token or create and persist one for a local first run."""
    configured = os.getenv("REPORTING_ADMIN_TOKEN", "").strip()
    stored = _load_private_secrets()
    token = configured or stored["admin_token"] or secrets.token_urlsafe(32)
    if not configured and token != stored["admin_token"]:
        stored["admin_token"] = token
        _save_private_secrets(stored)
        LOG.info("Generated a persistent admin token in the private secrets file")
    os.environ["REPORTING_ADMIN_TOKEN"] = token
    return token


def load_local_env_file() -> None:
    """Load simple KEY=VALUE settings without requiring python-dotenv.

    REPORTING_SKIP_ENV_FILE=1 skips it: the test suite sets this, so tests never pick up a developer's real
    storage database, secrets key or tokens from .env."""
    if os.getenv("REPORTING_SKIP_ENV_FILE", "").strip() == "1":
        return
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)
