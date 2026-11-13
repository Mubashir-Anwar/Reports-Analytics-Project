"""Local run history (audit records) of every generated report."""
from __future__ import annotations

import json
from collections import deque
from typing import Any

from backend.app.storage.store import get_store

AUDIT_KEY = "audit/report-executions.jsonl"


def _append_report_audit(audit: dict[str, Any]) -> None:
    """Persist one audit event locally (files) or in the storage database, without MongoDB write access."""
    get_store().append_line(AUDIT_KEY, json.dumps(audit, ensure_ascii=False, default=str, separators=(",", ":")))


def read_audit_records(limit: int = 200) -> list[dict[str, Any]]:
    records: deque[dict[str, Any]] = deque(maxlen=max(1, min(limit, 5000)))
    for line in get_store().lines(AUDIT_KEY):
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return list(reversed(records))
