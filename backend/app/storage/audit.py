"""Local run history (audit records) of every generated report."""
from __future__ import annotations

import json
import os
from collections import deque
from typing import Any

from backend.app.storage.files import report_audit_file


def _append_report_audit(audit: dict[str, Any]) -> None:
    """Persist one audit event locally without requiring MongoDB write access."""
    path = report_audit_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(audit, ensure_ascii=False, default=str, separators=(",", ":")) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8", newline="\n") as stream:
        stream.write(line)


def read_audit_records(limit: int = 200) -> list[dict[str, Any]]:
    path = report_audit_file()
    if not path.exists():
        return []
    records: deque[dict[str, Any]] = deque(maxlen=max(1, min(limit, 5000)))
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                records.append(item)
    return list(reversed(records))
