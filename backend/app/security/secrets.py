"""The service's secrets (admin token, data source URLs and passwords, schedule SFTP / API passwords) and the
optional local .env file.

Since schema v2 step 3 every secret is a row of the secrets table (owner type, owner id, name, value), encrypted
with REPORTING_SECRETS_KEY when it is set. The rest of the service still reads and writes them as one dict:
{"admin_token", "mongo_connections": {source id: {uri, …}}, "delivery_secrets": {schedule: {target: {name: value}}}}.
The older private_secrets.json document is read until the first save (or start-up) turns it into rows; it is
then kept under migrated/."""
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
ENV_FILE_KEYS: set[str] = set()   # settings that came from .env (not from the real environment)


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


SYSTEM, SERVICE = "system", "service"


def _row_id(owner_type: str, owner_id: str, name: str) -> str:
    return hashlib.sha256(f"{owner_type}|{owner_id}|{name}".encode("utf-8")).hexdigest()[:32]


def _rows() -> list[dict[str, Any]]:
    from backend.app.db import entities as ent  # noqa: PLC0415
    return ent.get_entities().find(ent.SECRETS)


def _open(row: dict[str, Any], cipher: Any) -> Any:
    value = row["encryptedValue"]
    if row.get("keyVersion") == ENCRYPTED_FORMAT:
        if cipher is None:
            raise RuntimeError("The secrets are encrypted: set REPORTING_SECRETS_KEY (the same key as when they were saved)")
        from cryptography.fernet import InvalidToken
        try:
            return json.loads(cipher.decrypt(str(value).encode("ascii")))
        except InvalidToken:
            raise RuntimeError("REPORTING_SECRETS_KEY does not open the secrets (wrong key?)") from None
    return value


def _from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cipher = _cipher()
    out: dict[str, Any] = {"admin_token": "", "mongo_connections": {}}
    delivery: dict[str, dict[str, dict[str, Any]]] = {}
    plain = False
    for row in rows:
        value = _open(row, cipher)
        plain = plain or row.get("keyVersion") != ENCRYPTED_FORMAT
        if row["ownerType"] == SYSTEM:
            out[row["secretName"]] = value
        elif row["ownerType"] == "data_source":
            out["mongo_connections"].setdefault(row["ownerId"], {})[row["secretName"]] = value
        elif row["ownerType"] == "schedule_target" and "/" in row["ownerId"]:
            job, target = row["ownerId"].split("/", 1)
            delivery.setdefault(job, {}).setdefault(target, {})[row["secretName"]] = value
    if delivery:
        out["delivery_secrets"] = delivery
    out["admin_token"] = str(out.get("admin_token") or "").strip()
    if plain and cipher is not None:
        _save_private_secrets(out)               # first start with a key: encrypt what was stored in plain text
        LOG.info("The secrets are now encrypted with REPORTING_SECRETS_KEY")
    return out


def secrets_encrypted() -> bool:
    rows = _rows()
    return bool(rows) and all(row.get("keyVersion") == ENCRYPTED_FORMAT for row in rows)


def _load_private_secrets() -> dict[str, Any]:
    rows = _rows()
    if rows:
        return _from_rows(rows)
    raw = get_store().read_json("private_secrets.json")       # the older document, until the first save
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

    connections = raw.get("mongo_connections", {})
    if not isinstance(connections, dict):
        raise ValueError("mongo_connections in private_secrets.json must be a JSON object")
    delivery = raw.get("delivery_secrets") or {}
    return {
        "admin_token": str(raw.get("admin_token", "")).strip(),
        "mongo_connections": connections,
        **({"delivery_secrets": delivery} if isinstance(delivery, dict) and delivery else {}),   # schedule SFTP / API passwords
    }


def _wanted_rows(payload: dict[str, Any]) -> dict[tuple[str, str, str], Any]:
    wanted: dict[tuple[str, str, str], Any] = {}
    for name, value in payload.items():
        if name == "mongo_connections":
            for source_id, fields in (value or {}).items():
                for field, item in (fields or {}).items() if isinstance(fields, dict) else ():
                    wanted[("data_source", str(source_id), str(field))] = item
        elif name == "delivery_secrets":
            for job, targets in (value or {}).items():
                for target, fields in (targets or {}).items() if isinstance(targets, dict) else ():
                    for field, item in (fields or {}).items() if isinstance(fields, dict) else ():
                        wanted[("schedule_target", f"{job}/{target}", str(field))] = item
        elif value not in (None, ""):
            wanted[(SYSTEM, SERVICE, str(name))] = value
    return wanted


def _save_private_secrets(payload: dict[str, Any], *, by: str = "") -> None:
    """Make the secrets table equal to this dict: one row per secret, encrypted when REPORTING_SECRETS_KEY is set
    (recommended: then a copy of the data folder or of the storage database alone reveals no password).
    Files backend: records/tables/secrets.json, mode 0600 on Unix."""
    from backend.app.db import entities as ent  # noqa: PLC0415
    from backend.app.db import model  # noqa: PLC0415
    cipher = _cipher()
    db = ent.get_entities()
    current = {row["secretId"]: row for row in db.find(ent.SECRETS)}
    wanted = _wanted_rows(payload)
    keep = set()
    for (owner_type, owner_id, name), value in wanted.items():
        secret_id = _row_id(owner_type, owner_id, name)
        keep.add(secret_id)
        old = current.get(secret_id)
        if old is not None and old.get("keyVersion") == (ENCRYPTED_FORMAT if cipher else "plain"):
            try:
                if _open(old, cipher) == value:
                    continue                                   # unchanged: no new ciphertext, no write
            except RuntimeError:
                pass
        stored = cipher.encrypt(json.dumps(value, ensure_ascii=False).encode("utf-8")).decode("ascii") if cipher else value
        db.put(ent.SECRETS, {"secretId": secret_id, "ownerType": owner_type, "ownerId": owner_id[:120], "secretName": name[:120],
                             "encryptedValue": stored, "keyVersion": ENCRYPTED_FORMAT if cipher else "plain",
                             "updatedBy": by or None, "updatedAt": model.now()})
    for secret_id in set(current) - keep:
        db.delete(ent.SECRETS, secret_id)
    store = get_store()
    old_document = store.read_json("private_secrets.json")
    if old_document is not None:                                # the older document: kept aside, no longer read
        store.write_json("migrated/private_secrets.json", old_document, private=True)
        store.delete("private_secrets.json")


def upgrade_secrets() -> int:
    """Start-up: the older private_secrets.json becomes rows of the secrets table (once)."""
    if _rows() or get_store().read_json("private_secrets.json") is None:
        return 0
    payload = _load_private_secrets()
    _save_private_secrets(payload, by="upgrade")
    LOG.info("Schema v2 (secrets): %d secrets moved into the secrets table", len(_rows()))
    return len(_rows())


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
            if key not in os.environ:
                ENV_FILE_KEYS.add(key)
            os.environ.setdefault(key, value)
