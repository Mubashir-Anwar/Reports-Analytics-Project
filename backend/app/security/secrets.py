"""private_secrets.json (admin token, adapter secrets) and the optional local .env file."""
from __future__ import annotations

import os
import secrets
from typing import Any

from backend.app.constants import PROJECT_ROOT
from backend.app.logs import LOG
from backend.app.storage.store import get_store


def _load_private_secrets() -> dict[str, Any]:
    raw = get_store().read_json("private_secrets.json")
    if raw is None:
        return {"admin_token": "", "mongo_connections": {}}
    if not isinstance(raw, dict):
        raise ValueError("The root of private_secrets.json must be a JSON object")
    connections = raw.get("mongo_connections", {})
    if not isinstance(connections, dict):
        raise ValueError("mongo_connections in private_secrets.json must be a JSON object")
    return {
        "admin_token": str(raw.get("admin_token", "")).strip(),
        "mongo_connections": connections,
    }


def _save_private_secrets(payload: dict[str, Any]) -> None:
    # files backend: mode 0600 on Unix (Windows inherits the current user's ACL); PostgreSQL: a private row
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
    """Load simple KEY=VALUE settings without requiring python-dotenv."""
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
