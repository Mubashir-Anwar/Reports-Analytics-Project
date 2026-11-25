"""Own format patterns (name + Java pattern), shared by everyone who designs reports on this server: settings/patterns.json.

A report keeps the pattern itself, so changing or removing a saved one never changes a report.
"""
from __future__ import annotations

import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.app.expressions import jexpr
from backend.app.rendering.engine import is_date_pattern
from backend.app.storage.store import get_store

KEY = "settings/patterns.json"
MAX_PATTERNS = 200
MAX_LENGTH = 60            # what a text field keeps
_LOCK = threading.RLock()


def list_patterns() -> list[dict[str, Any]]:
    """[{id, name, pattern, kind: "date" | "number", by, at}], by name."""
    items = [item for item in (get_store().read_json(KEY) or {}).get("patterns") or [] if isinstance(item, dict) and item.get("id")]
    return sorted(items, key=lambda item: str(item.get("name")).casefold())


def check(pattern: str) -> str:
    """The kind of a pattern; ValueError with the reason when it formats nothing."""
    if not pattern:
        raise ValueError("Write a pattern")
    if len(pattern) > MAX_LENGTH:
        raise ValueError(f"A pattern may have at most {MAX_LENGTH} characters")
    if re.search(r"[\x00-\x1f]", pattern):
        raise ValueError("A pattern is one line of text")
    try:
        if is_date_pattern(pattern):
            jexpr.DateFormatter(pattern).format(datetime(2026, 11, 19, 14, 5, 9))
            return "date"
        if re.search(r"[#0]", re.sub(r"'[^']*'", "", pattern)):
            jexpr.DecimalFormatter(pattern).format(-1234.5)
            return "number"
    except (jexpr.JExprError, ArithmeticError, ValueError, TypeError) as exc:
        raise ValueError(str(exc) or "The pattern could not be understood") from None
    raise ValueError("Use # or 0 for a number, or date letters such as dd/MM/yyyy")


def save_pattern(name: str, pattern: str, by: str = "", replace: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add a pattern, or change `replace` (an entry of the list)."""
    name, pattern = " ".join(str(name or "").split()), str(pattern or "").strip()
    if not name or len(name) > 60 or re.search(r"[<>\x00-\x1f]", name):
        raise ValueError("Give the pattern a name (at most 60 characters, without < and >)")
    kind = check(pattern)
    with _LOCK:
        kept = [item for item in list_patterns() if not replace or item["id"] != replace["id"]]
        if any(item["name"].casefold() == name.casefold() for item in kept):
            raise ValueError(f"There is already a pattern called {name}")
        if len(kept) >= MAX_PATTERNS:
            raise ValueError(f"At most {MAX_PATTERNS} own patterns")
        item = {"id": replace["id"] if replace else uuid.uuid4().hex, "name": name, "pattern": pattern, "kind": kind,
                "by": replace.get("by", "") if replace else by, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        get_store().write_json(KEY, {"patterns": [*kept, item]})
    return item


def delete_pattern(pattern_id: str) -> None:
    with _LOCK:
        get_store().write_json(KEY, {"patterns": [item for item in list_patterns() if item["id"] != pattern_id]})
