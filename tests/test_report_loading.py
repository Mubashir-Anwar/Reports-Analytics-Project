"""Report definitions on disk: a broken file is skipped instead of stopping the app."""
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import service_names as core  # noqa: E402

GOOD = {"id": "GOOD_REPORT", "collection": "orders", "pipeline": [{"$match": {}}], "columns": ["a"]}


def _write(directory: Path, relative: str, content) -> Path:
    path = directory / "reports" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return path


def test_invalid_files_are_skipped_and_logged(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path))
    _write(tmp_path, "General/GOOD_REPORT.json", GOOD)
    _write(tmp_path, "General/EMPTY_COLLECTION.json", {"id": "BROKEN", "collection": ""})
    _write(tmp_path, "General/NOT_JSON.json", "{ this is not json")
    _write(tmp_path, "General/LIST_ROOT.json", [1, 2, 3])
    _write(tmp_path, "General/BAD_ENCODING.json", b"\xff\xfe\x00garbage")
    _write(tmp_path, "General/BAD_PARAMS.json", {**GOOD, "id": "BAD_PARAMS", "parameters": [{"name": "x", "maxLength": "abc"}]})

    with caplog.at_level(logging.ERROR, logger="mongo_reporting_service"):
        registry = core.discover_reports()

    assert list(registry) == ["GOOD_REPORT"]
    skipped = [record.getMessage() for record in caplog.records if "Skipping invalid report definition" in record.getMessage()]
    assert len(skipped) == 5


def test_duplicate_report_id_is_logged(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path))
    _write(tmp_path, "A/GOOD_REPORT.json", {**GOOD, "folder": "A", "title": "from A"})
    _write(tmp_path, "B/GOOD_REPORT.json", {**GOOD, "folder": "B", "title": "from B"})

    with caplog.at_level(logging.WARNING, logger="mongo_reporting_service"):
        registry = core.discover_reports()

    assert registry["GOOD_REPORT"].title == "from B"  # unchanged behaviour: the last file in path order wins
    assert any("Duplicate report ID GOOD_REPORT" in record.getMessage() for record in caplog.records)


def test_app_starts_with_a_broken_report_file(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPORTING_ADMIN_TOKEN", "test-token")
    _write(tmp_path, "General/BROKEN.json", {"id": "BROKEN", "collection": ""})
    _write(tmp_path, "General/GOOD_REPORT.json", GOOD)

    from fastapi.testclient import TestClient

    with TestClient(core.make_app()) as client:
        listed = client.get("/reports", headers={"X-Admin-Token": "test-token"}).text
    assert "GOOD_REPORT" in listed
    assert "BROKEN" not in listed
