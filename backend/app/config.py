"""Settings read from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass

from backend.app.logs import LOG
from backend.app.security.secrets import load_local_env_file


@dataclass(frozen=True)
class Settings:
    mongo_uri: str
    mongo_database: str
    audit_database: str
    audit_collection: str
    max_time_ms: int
    max_rows: int
    allow_disk_use: bool
    audit_to_mongodb: bool
    audit_required: bool = False
    # capacity (services/capacity.py): reports made at the same time in this process, per caller, and how long a
    # request waits for a free place before "busy, try again" (503)
    max_concurrent: int = 4
    max_per_caller: int = 2
    queue_wait_seconds: int = 15
    # big CSV / Excel files made in the background are written to disk while the rows are read (rendering/spool.py)
    export_max_rows: int = 1_000_000
    export_max_time_ms: int = 600_000

    @classmethod
    def from_env(cls) -> "Settings":
        load_local_env_file()
        uri = os.getenv("MONGODB_URI", "")
        database = os.getenv("MONGODB_DATABASE", "")
        if not uri or not database:
            # Permit importing the app without configured secrets; first DB request returns a clear error.
            LOG.warning("MONGODB_URI/MONGODB_DATABASE are not configured; report execution is disabled")
        return cls(
            mongo_uri=uri,
            mongo_database=database,
            audit_database=os.getenv("AUDIT_DATABASE", database),
            audit_collection=os.getenv("AUDIT_COLLECTION", "report_execution_audit"),
            max_time_ms=int(os.getenv("REPORT_MAX_TIME_MS", "30000")),
            max_rows=int(os.getenv("REPORT_MAX_ROWS", "10000")),
            allow_disk_use=os.getenv("REPORT_ALLOW_DISK_USE", "false").lower() == "true",
            audit_to_mongodb=os.getenv("AUDIT_TO_MONGODB", "false").lower() == "true",
            audit_required=os.getenv("AUDIT_REQUIRED", "false").lower() == "true",
            max_concurrent=_int("REPORT_MAX_CONCURRENT", max(2, min(8, os.cpu_count() or 2)), 1, 256),
            max_per_caller=_int("REPORT_MAX_PER_CALLER", 2, 1, 64),
            queue_wait_seconds=_int("REPORT_QUEUE_WAIT_SECONDS", 15, 0, 300),
            export_max_rows=_int("REPORT_EXPORT_MAX_ROWS", 1_000_000, 1000, 50_000_000),
            export_max_time_ms=_int("REPORT_EXPORT_MAX_TIME_MS", 600_000, 10_000, 86_400_000),
        )


def _int(name: str, default: int, low: int, high: int) -> int:
    """A whole-number setting kept between low and high; a wrong value falls back to the default (and is logged)."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(low, min(high, int(raw)))
    except ValueError:
        LOG.warning("%s=%r is not a whole number; using %d", name, raw, default)
        return default
