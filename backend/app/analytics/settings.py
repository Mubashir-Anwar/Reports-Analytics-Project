"""Analytics settings (environment variables, read when used so a restart is not needed in tests).

    REPORTING_ANALYTICS_DIR            where the Parquet files are kept (default <data directory>/analytics)
    ANALYTICS_IMPORT_MAX_ROWS          most records one dataset may import (default 5,000,000)
    ANALYTICS_IMPORT_MAX_TIME_MS       query time limit of one import (default 30 minutes)
    ANALYTICS_QUERY_MAX_ROWS           most result rows one visual may get back (default 5,000)
    ANALYTICS_QUERY_TIMEOUT_SECONDS    a visual's query is stopped after this (default 30)
    ANALYTICS_MEMORY_LIMIT             DuckDB memory per query, e.g. 512MB or 2GB (default 1GB; it spills to disk)
    ANALYTICS_THREADS                  DuckDB threads per query (default 2)
    ANALYTICS_REFRESH_WORKERS          imports at the same time (default 1)
    ANALYTICS_MIN_REFRESH_MINUTES      shortest scheduled refresh interval (default 15)
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from backend.app.logs import LOG
from backend.app.storage.files import data_directory


def _int(name: str, default: int, low: int, high: int) -> int:
    from backend.app import limits  # noqa: PLC0415
    if name in limits.BY_ENV:
        return int(limits.value(name))               # Admin → Limits, else the environment variable
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(low, min(high, int(raw)))
    except ValueError:
        LOG.warning("%s=%r is not a whole number; using %d", name, raw, default)
        return default


def directory() -> Path:
    configured = os.getenv("REPORTING_ANALYTICS_DIR", "").strip()
    folder = Path(configured).expanduser().resolve() if configured else data_directory() / "analytics"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def import_max_rows() -> int:
    return _int("ANALYTICS_IMPORT_MAX_ROWS", 5_000_000, 1, 500_000_000)


def import_max_time_ms() -> int:
    return _int("ANALYTICS_IMPORT_MAX_TIME_MS", 1_800_000, 10_000, 86_400_000)


def query_max_rows() -> int:
    return _int("ANALYTICS_QUERY_MAX_ROWS", 5000, 10, 1_000_000)


def query_timeout_seconds() -> int:
    return _int("ANALYTICS_QUERY_TIMEOUT_SECONDS", 30, 1, 3600)


def memory_limit() -> str:
    from backend.app import limits  # noqa: PLC0415
    raw = str(limits.value("ANALYTICS_MEMORY_LIMIT")).strip().upper().replace(" ", "")
    if raw and re.fullmatch(r"\d{1,6}(KB|MB|GB|TB)", raw):
        return raw
    if raw:
        LOG.warning("ANALYTICS_MEMORY_LIMIT=%r is not like 512MB or 2GB; using 1GB", raw)
    return "1GB"


def threads() -> int:
    return _int("ANALYTICS_THREADS", 2, 1, 64)


def refresh_workers() -> int:
    return _int("ANALYTICS_REFRESH_WORKERS", 1, 1, 8)


def min_refresh_minutes() -> int:
    return _int("ANALYTICS_MIN_REFRESH_MINUTES", 15, 1, 1440)


def limits() -> dict[str, object]:
    return {"importMaxRows": import_max_rows(), "importMaxTimeMs": import_max_time_ms(), "queryMaxRows": query_max_rows(),
            "queryTimeoutSeconds": query_timeout_seconds(), "memoryLimit": memory_limit(), "threads": threads(),
            "refreshWorkers": refresh_workers(), "minRefreshMinutes": min_refresh_minutes()}
