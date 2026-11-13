"""Service log: format, rotating log file, reading log entries for /admin → Logs."""
from __future__ import annotations

import logging
import os
import re
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

from backend.app.storage.files import data_directory

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
LOG = logging.getLogger("mongo_reporting_service")
HTTP_LOG = logging.getLogger("mongo_reporting_service.http")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format=LOG_FORMAT)
LOG_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) (DEBUG|INFO|WARNING|ERROR|CRITICAL) \[([^\]]+)\] (.*)$")
LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
_FILE_HANDLER: logging.Handler | None = None


# ----------------------------------------------------------------- service log file
def log_file_path() -> Path:
    configured = os.getenv("REPORTING_LOG_FILE", "").strip()
    return Path(configured).expanduser().resolve() if configured else data_directory() / "logs" / "service.log"


def configure_file_logging() -> Path | None:
    """Write the service log to a rotating file (default <data dir>/logs/service.log).

    REPORTING_LOG_TO_FILE=false turns it off (e.g. when the platform collects stdout instead).
    Only the path of a request is logged, never its query string, headers or MongoDB URLs.
    """
    global _FILE_HANDLER
    level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    LOG.setLevel(level if level in LOG_LEVELS else "INFO")  # independent of how the host configured the root logger
    if os.getenv("REPORTING_LOG_TO_FILE", "true").strip().lower() == "false":
        return None
    path = log_file_path()
    if _FILE_HANDLER is not None and getattr(_FILE_HANDLER, "baseFilename", "") == str(path):
        return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=max(100_000, int(os.getenv("REPORTING_LOG_MAX_BYTES", "5000000"))),
            backupCount=max(1, int(os.getenv("REPORTING_LOG_BACKUPS", "5"))),
            encoding="utf-8",
        )
    except (OSError, ValueError):
        LOG.exception("Service log file could not be opened at %s", path)
        return None
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.setLevel(level if level in LOG_LEVELS else "INFO")
    for name in ("mongo_reporting_service", "uvicorn.error"):
        logger = logging.getLogger(name)
        if _FILE_HANDLER is not None:
            logger.removeHandler(_FILE_HANDLER)
        logger.addHandler(handler)
    if _FILE_HANDLER is not None:
        _FILE_HANDLER.close()
    _FILE_HANDLER = handler
    return path


def read_log_entries(limit: int = 300, min_level: str = "DEBUG", search: str = "") -> list[dict[str, str]]:
    """Newest-last log entries from the current log file; traceback lines stay with their entry."""
    path = log_file_path()
    if not path.exists():
        return []
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - 4_000_000))
        text = stream.read().decode("utf-8", errors="replace")
    threshold = LOG_LEVELS.get(min_level.upper(), 10)
    needle = search.strip().lower()
    entries: deque[dict[str, str]] = deque(maxlen=max(1, min(limit, 5000)))
    current: dict[str, str] | None = None

    def flush() -> None:
        if current is None or LOG_LEVELS.get(current["level"], 20) < threshold:
            return
        if needle and needle not in (current["message"] + current["logger"]).lower():
            return
        entries.append(current)

    for line in text.splitlines():
        match = LOG_LINE.match(line)
        if match:
            flush()
            current = {"time": match.group(1), "level": match.group(2), "logger": match.group(3), "message": match.group(4)}
        elif current is not None:
            current["message"] += "\n" + line
    flush()
    return list(entries)
