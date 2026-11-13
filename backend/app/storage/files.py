"""Where data lives (REPORTING_DATA_DIR) and the file helpers: folders, atomic JSON writes, first start."""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from backend.app.constants import PROJECT_ROOT, REPORT_ID_PATTERN


def data_directory() -> Path:
    configured = os.getenv("REPORTING_DATA_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else PROJECT_ROOT / "data"


def report_definitions_directory() -> Path:
    return data_directory() / "reports"


def workspace_directory() -> Path:
    """The designer's own saved drafts. Nothing here is on the server until it is published."""
    return data_directory() / "workspace"


def users_file() -> Path:
    return data_directory() / "users.json"


def connections_file() -> Path:
    return data_directory() / "connections.json"


def folders_file() -> Path:
    return data_directory() / "folders.json"


def private_secrets_file() -> Path:
    return data_directory() / "private_secrets.json"


def report_audit_file() -> Path:
    return data_directory() / "audit" / "report-executions.jsonl"


def normalize_folder_path(value: Any) -> str:
    folder = str(value or "General").strip().replace("\\", "/").strip("/") or "General"
    parts = folder.split("/")
    if len(folder) > 240 or any(
        part in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,63}", part)
        for part in parts
    ):
        raise ValueError("Use only letters, numbers, spaces, underscores and hyphens in a folder name")
    return "/".join(parts)


def report_definition_path(report_id: str, folder: str) -> Path:
    return report_definitions_directory().joinpath(*folder.split("/"), f"{report_id}.json")


def list_saved_folders() -> list[str]:
    found = {"General"}
    path = folders_file()
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        items = raw.get("folders", []) if isinstance(raw, dict) else []
        if not isinstance(items, list):
            raise ValueError("folders in folders.json must be an array")
        for item in items:
            normalized = normalize_folder_path(item)
            pieces = normalized.split("/")
            found.update("/".join(pieces[:index]) for index in range(1, len(pieces) + 1))
    directory = report_definitions_directory()
    if directory.exists():
        for definition in directory.rglob("*.json"):
            relative_parent = definition.parent.relative_to(directory)
            if str(relative_parent) == ".":
                continue
            pieces = list(relative_parent.parts)
            found.update("/".join(pieces[:index]) for index in range(1, len(pieces) + 1))
    return sorted(found, key=lambda item: (item.casefold(), item))


def add_saved_folder(folder: str) -> list[str]:
    """Register a folder (and its parents) in folders.json and create its directory."""
    folders = set(list_saved_folders())
    pieces = folder.split("/")
    folders.update("/".join(pieces[:index]) for index in range(1, len(pieces) + 1))
    _write_json_atomically(folders_file(), {"folders": sorted(folders, key=str.casefold)})
    report_definition_path(".folder", folder).parent.mkdir(parents=True, exist_ok=True)
    return list_saved_folders()


def initialize_data_directory() -> None:
    """Copy bundled starter definitions into a new persistent data mount once."""
    target = data_directory()
    bundled = PROJECT_ROOT / "data"
    target_reports = target / "reports"
    target_reports.mkdir(parents=True, exist_ok=True)
    # Migrate existing flat definitions before copying starter definitions, so
    # a previously edited report takes precedence over the bundled starter.
    for legacy_path in list(target_reports.glob("*.json")):
        try:
            payload = json.loads(legacy_path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict):
                continue
            report_id = str(payload.get("id", "")).strip()
            folder = normalize_folder_path(payload.get("folder", "General"))
            if not REPORT_ID_PATTERN.fullmatch(report_id):
                continue
        except (OSError, ValueError, AttributeError, TypeError):
            continue
        payload["folder"] = folder
        destination = report_definition_path(report_id, folder)
        if destination != legacy_path:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                _write_json_atomically(destination, payload)
            legacy_path.unlink()
        else:
            _write_json_atomically(legacy_path, payload)
    bundled_reports = bundled / "reports"
    if bundled_reports.exists():
        for source in bundled_reports.rglob("*.json"):
            destination = target_reports / source.relative_to(bundled_reports)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copy2(source, destination)
    source_connections = bundled / "connections.json"
    destination_connections = target / "connections.json"
    if source_connections.exists() and not destination_connections.exists():
        shutil.copy2(source_connections, destination_connections)


def _write_json_atomically(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
