"""Limits an administrator sets in Admin → Limits: how many reports at once, rows, time limits, analytics, API keys.

Each limit has an environment variable (its starting value, as before). A value saved in Admin is kept in the system
database (settings/limits.json), wins over the environment variable and takes effect without a restart: every server
behind a load balancer reads it again within LIVE_SECONDS. "Reset" removes the saved value, so the environment
variable (or the built-in default) counts again.
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

KEY = "settings/limits.json"
LIVE_SECONDS = 10
_CACHE: dict[str, Any] = {"at": 0.0, "value": None}
_LOCK = threading.Lock()
MEMORY_RE = re.compile(r"^\d{1,6}(KB|MB|GB|TB)$")


@dataclass(frozen=True)
class Limit:
    env: str                    # the environment variable (also the key in settings/limits.json)
    group: str                  # reports | downloads | analytics | api
    label: str
    help: str
    default: int | str
    low: int = 0
    high: int = 0
    unit: str = ""              # shown next to the box: reports, rows, ms, seconds, minutes, MB …
    kind: str = "int"           # int | memory


def _cpu_default() -> int:
    return max(2, min(8, os.cpu_count() or 2))


LIMITS: tuple[Limit, ...] = (
    Limit("REPORT_MAX_CONCURRENT", "reports", "Reports at the same time",
          "How many reports this server makes at once (each server process). More use more memory.", _cpu_default(), 1, 256, "reports"),
    Limit("REPORT_MAX_PER_CALLER", "reports", "Reports at once per user",
          "One user may make this many at the same time; an API key may use up to half of all places.", 2, 1, 64, "reports"),
    Limit("REPORT_QUEUE_WAIT_SECONDS", "reports", "Wait for a free place",
          "When every place is taken a request waits this long, then answers 503 busy.", 15, 0, 300, "seconds"),
    Limit("REPORT_MAX_ROWS", "reports", "Records in one report",
          "A report that would read more is refused (CSV and Excel then go to the background export).", 10000, 1, 10_000_000, "records"),
    Limit("REPORT_MAX_TIME_MS", "reports", "Query time limit",
          "The database query of one report is stopped after this.", 30000, 1000, 3_600_000, "ms"),
    Limit("REPORT_EXPORT_MAX_ROWS", "reports", "Records in a background export",
          "Big CSV / Excel files made in the background may have up to this many records.", 1_000_000, 1000, 50_000_000, "records"),
    Limit("REPORT_EXPORT_MAX_TIME_MS", "reports", "Background export time limit",
          "A background export is stopped after this.", 600_000, 10_000, 86_400_000, "ms"),
    Limit("REPORTING_LOGIN_MAX_FAILURES", "signin", "Wrong passwords before a lock",
          "After this many wrong passwords in a row an account cannot sign in for the lock time. The sign-in page "
          "warns when 3 or fewer tries are left. A new password (Admin → Users or Forgot password) unlocks it.", 5, 1, 100, "tries"),
    Limit("REPORTING_LOGIN_LOCK_MINUTES", "signin", "Lock time",
          "How long a locked account (or a computer trying many accounts) has to wait.", 15, 1, 1440, "minutes"),
    Limit("REPORTING_DELIVERY_KEEP_MINUTES", "downloads", "Keep finished downloads",
          "Files asked for with X-Deliver: later are kept this long for collecting.", 60, 5, 10080, "minutes"),
    Limit("REPORTING_API_RATE_PER_MINUTE", "api", "Calls per minute per API key",
          "The default for keys without their own limit (0 = no limit).", 120, 0, 100_000, "calls"),
    Limit("REPORTING_MAX_BODY_MB", "api", "Largest request",
          "Uploads and requests larger than this are refused with 413.", 25, 1, 500, "MB"),
    Limit("ANALYTICS_IMPORT_MAX_ROWS", "analytics", "Records in one dataset",
          "An import stops with an error above this.", 5_000_000, 1, 500_000_000, "records"),
    Limit("ANALYTICS_IMPORT_MAX_TIME_MS", "analytics", "Import time limit",
          "The query of one import is stopped after this.", 1_800_000, 10_000, 86_400_000, "ms"),
    Limit("ANALYTICS_QUERY_MAX_ROWS", "analytics", "Rows one visual gets back",
          "A visual with more rows shows the first ones and says so.", 5000, 10, 1_000_000, "rows"),
    Limit("ANALYTICS_QUERY_TIMEOUT_SECONDS", "analytics", "Visual query time limit",
          "A dashboard visual's query is stopped after this.", 30, 1, 3600, "seconds"),
    Limit("ANALYTICS_MEMORY_LIMIT", "analytics", "Memory per query",
          "DuckDB memory for one query, e.g. 512MB or 2GB; above it, it spills to disk.", "1GB", unit="", kind="memory"),
    Limit("ANALYTICS_THREADS", "analytics", "Threads per query", "CPU threads one analytics query may use.", 2, 1, 64, "threads"),
    Limit("ANALYTICS_REFRESH_WORKERS", "analytics", "Imports at the same time",
          "How many datasets are imported at once (a restart applies a new value).", 1, 1, 8, "imports"),
    Limit("ANALYTICS_MIN_REFRESH_MINUTES", "analytics", "Shortest refresh schedule",
          "A dataset cannot be scheduled to import more often than this.", 15, 1, 1440, "minutes"),
)
BY_ENV = {item.env: item for item in LIMITS}
GROUPS = (("signin", "Sign-in"), ("reports", "Reports"), ("downloads", "Downloads"), ("analytics", "Analytics"), ("api", "API and uploads"))


def _store() -> Any:
    from backend.app.storage.store import get_store  # noqa: PLC0415 - the store needs the settings first
    return get_store()


def saved() -> dict[str, Any]:
    """What Admin saved (read again at most every LIVE_SECONDS)."""
    with _LOCK:
        if _CACHE["value"] is not None and time.monotonic() - _CACHE["at"] < LIVE_SECONDS:
            return dict(_CACHE["value"])
    try:
        stored = _store().read_json(KEY) or {}
    except Exception:  # noqa: BLE001 - a limit must never stop a report: fall back to the environment
        stored = {}
    value = {key: item for key, item in (stored.get("values") or {}).items() if key in BY_ENV}
    with _LOCK:
        _CACHE.update(at=time.monotonic(), value=value)
    return dict(value)


def _clean(item: Limit, raw: Any) -> int | str:
    if item.kind == "memory":
        text = str(raw).strip().upper().replace(" ", "")
        if not MEMORY_RE.fullmatch(text):
            raise ValueError(f"{item.label}: write it like 512MB or 2GB")
        return text
    try:
        number = int(str(raw).strip())
    except ValueError:
        raise ValueError(f"{item.label}: a whole number") from None
    if not item.low <= number <= item.high:
        raise ValueError(f"{item.label}: between {item.low:,} and {item.high:,}")
    return number


def from_environment(item: Limit) -> int | str:
    raw = os.getenv(item.env, "").strip()
    if not raw:
        return item.default
    try:
        return _clean(item, raw)
    except ValueError:
        if item.kind == "int":
            try:
                return max(item.low, min(item.high, int(raw)))     # out of range: kept inside it, as before
            except ValueError:
                pass
        return item.default


def value(env: str) -> Any:
    """The limit now: saved in Admin, else the environment variable, else the default."""
    item = BY_ENV[env]
    chosen = saved().get(env)
    if chosen is not None:
        try:
            return _clean(item, chosen)
        except ValueError:
            pass
    return from_environment(item)


def describe() -> dict[str, Any]:
    """Every limit with its value now, where that comes from, and the environment / default value under it."""
    chosen = saved()
    items = []
    for item in LIMITS:
        env_value = from_environment(item)
        items.append({"key": item.env, "group": item.group, "label": item.label, "help": item.help, "unit": item.unit,
                      "kind": item.kind, "low": item.low, "high": item.high, "value": value(item.env),
                      "source": "admin" if item.env in chosen else ("environment" if os.getenv(item.env, "").strip() else "default"),
                      "fallback": env_value})
    stored = {}
    try:
        stored = _store().read_json(KEY) or {}
    except Exception:  # noqa: BLE001
        pass
    return {"groups": [{"id": gid, "label": label} for gid, label in GROUPS], "limits": items,
            "updatedAt": stored.get("updatedAt"), "updatedBy": stored.get("updatedBy"), "liveSeconds": LIVE_SECONDS}


def save(changes: dict[str, Any], *, by: str) -> dict[str, Any]:
    """changes: {ENV_NAME: value, or null to go back to the environment / default}. Checks everything first."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("Send the limits to change")
    current = saved()
    for key, raw in changes.items():
        if key not in BY_ENV:
            raise ValueError(f"There is no limit called {key}")
        if raw is None or raw == "":
            current.pop(key, None)
        else:
            current[key] = _clean(BY_ENV[key], raw)
    from backend.app.db import model  # noqa: PLC0415
    _store().write_json(KEY, {"values": current, "updatedAt": model.iso(model.now()), "updatedBy": by})
    with _LOCK:
        _CACHE.update(at=time.monotonic(), value=dict(current))
    return describe()


class LiveSettings:
    """ctx.settings with the report limits read live (Admin → Limits); everything else from the start-up settings."""

    LIVE = {"max_concurrent": "REPORT_MAX_CONCURRENT", "max_per_caller": "REPORT_MAX_PER_CALLER",
            "queue_wait_seconds": "REPORT_QUEUE_WAIT_SECONDS", "max_rows": "REPORT_MAX_ROWS", "max_time_ms": "REPORT_MAX_TIME_MS",
            "export_max_rows": "REPORT_EXPORT_MAX_ROWS", "export_max_time_ms": "REPORT_EXPORT_MAX_TIME_MS"}

    def __init__(self, base: Any) -> None:
        object.__setattr__(self, "_base", base)

    def __getattr__(self, name: str) -> Any:
        if name in LiveSettings.LIVE:
            return value(LiveSettings.LIVE[name])
        return getattr(object.__getattribute__(self, "_base"), name)
