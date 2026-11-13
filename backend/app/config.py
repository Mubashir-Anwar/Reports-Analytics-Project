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
        )
