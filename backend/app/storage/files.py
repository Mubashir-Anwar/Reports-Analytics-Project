"""Where data lives (REPORTING_DATA_DIR) and the file helpers: folders, atomic JSON writes, first start."""
from __future__ import annotations

import json
import os
import re
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


def report_definition_key(report_id: str, folder: str) -> str:
    """Storage key of a published report: reports/<folder>/<id>.json"""
    return "/".join(["reports", *folder.split("/"), f"{report_id}.json"])


def workspace_key(report_id: str, folder: str) -> str:
    return "/".join(["workspace", *folder.split("/"), f"{report_id}.json"])


def _with_parents(folder: str) -> set[str]:
    pieces = folder.split("/")
    return {"/".join(pieces[:index]) for index in range(1, len(pieces) + 1)}


def list_saved_folders() -> list[str]:
    from backend.app.storage.store import get_store
    store = get_store()
    found = {"General"}
    raw = store.read_json("folders.json")
    if raw is not None:
        items = raw.get("folders", []) if isinstance(raw, dict) else []
        if not isinstance(items, list):
            raise ValueError("folders in folders.json must be an array")
        for item in items:
            found.update(_with_parents(normalize_folder_path(item)))
    for folder in store.dirs("reports"):
        try:
            found.update(_with_parents(normalize_folder_path(folder)))
        except ValueError:
            continue
    return sorted(found, key=lambda item: (item.casefold(), item))


def save_folder_list(folders: list[str]) -> None:
    from backend.app.storage.store import get_store
    get_store().write_json("folders.json", {"folders": sorted(folders, key=str.casefold)})


def add_saved_folder(folder: str) -> list[str]:
    """Register a folder (and its parents) in folders.json and create it."""
    from backend.app.storage.store import get_store
    folders = set(list_saved_folders()) | _with_parents(folder)
    save_folder_list(list(folders))
    get_store().make_dir("/".join(["reports", *folder.split("/")]))
    return list_saved_folders()


def initialize_data_directory() -> None:
    """First start: the folders the service needs, and the bundled starter reports / adapters (copied once)."""
    from backend.app.storage.store import FileStore, get_store
    store = get_store()
    bundled = PROJECT_ROOT / "data"
    if isinstance(store, FileStore):
        target_reports = data_directory() / "reports"
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
    else:
        store.make_dir("reports")
    bundled_reports = bundled / "reports"
    if bundled_reports.exists() and bundled_reports.resolve() != (data_directory() / "reports").resolve():
        for source in bundled_reports.rglob("*.json"):
            key = "reports/" + source.relative_to(bundled_reports).as_posix()
            if not store.exists(key):
                try:
                    store.write_json(key, json.loads(source.read_text(encoding="utf-8-sig")))
                except (OSError, ValueError):
                    continue
    source_connections = bundled / "connections.json"
    if source_connections.exists() and not store.exists("connections.json"):
        try:
            store.write_json("connections.json", json.loads(source_connections.read_text(encoding="utf-8-sig")))
        except (OSError, ValueError):
            pass


def _write_json_atomically(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
