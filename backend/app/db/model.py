"""The system database: the records the service keeps about its own work, the same in every backend.

Two record types for now (more follow the same pattern):

* report run   (rs_report_runs)   one row per execution: who, from where (channel), which report and data
                                  source, masked parameters, result, rows, duration, output size and hash.
* audit event  (rs_audit_events)  append-only: sign-ins, accounts, rights, API keys, publishing, deleting,
                                  permissions, data sources. Each event stores the hash of the one before it,
                                  so a changed or removed event is detected (verify_chain).

Rules that keep the backends interchangeable (files, PostgreSQL, MySQL, SQL Server, Oracle, SQLite, MongoDB):
ids and timestamps are made here (not by the database), times are UTC, values are plain JSON types, and the
same field names are used everywhere (camelCase in Python and the API; snake_case columns in SQL).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_ORG = "default"

CHANNELS = ("runner", "server", "designer-preview", "designer-run", "schedule", "api", "signed-link")
STATUSES = ("success", "failed")
ACTOR_TYPES = ("user", "api-key", "token", "schedule", "system", "anonymous")

# field name -> (type, max length or None, required)
RUN_FIELDS: dict[str, tuple[str, int | None, bool]] = {
    "id": ("str", 32, True),
    "orgId": ("str", 64, True),
    "startedAt": ("time", None, True),
    "finishedAt": ("time", None, False),
    "durationMs": ("int", None, False),
    "channel": ("str", 32, True),
    "actor": ("str", 128, True),
    "actorType": ("str", 16, True),
    "reportId": ("str", 80, False),
    "reportTitle": ("str", 200, False),
    "folder": ("str", 240, False),
    "dataSource": ("str", 64, False),
    "format": ("str", 8, False),
    "parameters": ("json", None, False),
    "status": ("str", 16, True),
    "errorCode": ("str", 64, False),
    "errorMessage": ("str", 500, False),
    "rowCount": ("int", None, False),
    "outputBytes": ("int", None, False),
    "outputSha256": ("str", 64, False),
    "clientIp": ("str", 64, False),
    "requestPath": ("str", 500, False),
    "onBehalfOf": ("str", 128, False),      # the website's own user, when a website runs the report (GeneratedBy)
}

AUDIT_FIELDS: dict[str, tuple[str, int | None, bool]] = {
    "id": ("str", 32, True),
    "seq": ("int", None, True),
    "orgId": ("str", 64, True),
    "at": ("time", None, True),
    "actor": ("str", 128, True),
    "actorType": ("str", 16, True),
    "action": ("str", 64, True),
    "target": ("str", 240, False),
    "details": ("json", None, False),
    "clientIp": ("str", 64, False),
    "prevHash": ("str", 64, False),
    "hash": ("str", 64, True),
}

# What may be filtered on when listing (all of them indexed in every backend).
RUN_FILTERS = ("reportId", "actor", "channel", "status", "orgId")
AUDIT_FILTERS = ("action", "actor", "target", "orgId")


def now() -> datetime:
    current = datetime.now(timezone.utc)
    return current.replace(microsecond=current.microsecond // 1000 * 1000)   # milliseconds, like every backend


def iso(value: datetime | str | None) -> str | None:
    """One text form for every backend: 2026-11-16T10:20:30.123Z."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = parse_time(value)
    if value.tzinfo is None:                       # SQLite and some drivers return naive UTC
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def new_id() -> str:
    """32 hex characters, ordered by creation time (millisecond prefix), so ids sort like start times."""
    return f"{int(time.time() * 1000):012x}{secrets.token_hex(10)}"


def org_id() -> str:
    return (os.getenv("REPORTING_ORG_ID", "") or DEFAULT_ORG).strip()[:64] or DEFAULT_ORG


def _safe_json(value: Any, depth: int = 0) -> Any:
    """Plain JSON values only; keys that MongoDB treats specially ($x, a.b) are made safe."""
    if depth > 6:
        return str(value)[:200]
    if isinstance(value, dict):
        return {str(key).replace(".", "_").lstrip("$")[:80] or "_": _safe_json(item, depth + 1) for key, item in list(value.items())[:100]}
    if isinstance(value, (list, tuple, set)):
        return [_safe_json(item, depth + 1) for item in list(value)[:100]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, datetime):
        return iso(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)[:1000]


def clean(record: dict[str, Any], fields: dict[str, tuple[str, int | None, bool]]) -> dict[str, Any]:
    """Keep known fields, cut strings to their column length, check required ones and types."""
    out: dict[str, Any] = {}
    for name, (kind, size, required) in fields.items():
        value = record.get(name)
        if value is None or value == "":
            if required:
                raise ValueError(f"{name} is required")
            out[name] = None
            continue
        if kind == "str":
            out[name] = str(value)[:size]
        elif kind == "int":
            out[name] = int(value)
        elif kind == "time":
            out[name] = iso(value)
        else:
            out[name] = _safe_json(value)
    return out


def mask(value: Any) -> Any:
    """Hide most of a sensitive value (CNIC, account number): keep the first and last two characters."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [mask(item) for item in value]
    text = str(value)
    if len(text) <= 4:
        return "*" * len(text)
    return text[:2] + "*" * (len(text) - 4) + text[-2:]


def masked_parameters(values: dict[str, Any], sensitive: set[str]) -> dict[str, Any]:
    return {name: (mask(value) if name in sensitive else value) for name, value in (values or {}).items()}


def event_hash(event: dict[str, Any]) -> str:
    """SHA-256 over the event's content and the previous hash (the chain link)."""
    content = {name: event.get(name) for name in AUDIT_FIELDS if name not in ("id", "hash")}
    text = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chain_problems(events: list[dict[str, Any]]) -> list[str]:
    """Events in seq order: what is wrong with the chain ([] = intact)."""
    problems = []
    previous: dict[str, Any] | None = None
    for event in events:
        if previous is not None and event["seq"] != previous["seq"] + 1:
            problems.append(f"event {previous['seq'] + 1} is missing")
        if previous is not None and event.get("prevHash") != previous["hash"]:
            problems.append(f"event {event['seq']} does not follow event {previous['seq']}")
        if event_hash(event) != event.get("hash"):
            problems.append(f"event {event['seq']} was changed after it was written")
        previous = event
    return problems
