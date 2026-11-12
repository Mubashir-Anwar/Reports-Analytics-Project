"""Access to /generate and /reports, CSV formula prefixing, and $P{} collections needing allowedValues."""
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import designer_render as dr  # noqa: E402
import reporting_core as core  # noqa: E402

ADMIN = "admin-test-token"
SECRET = "s" * 40
REPORT = {
    "id": "SALES", "title": "Sales", "collection": "orders", "connectionId": "default",
    "parameters": [{"name": "Branch", "type": "string"}],
    "queryLanguage": "aggregation-json",
    "querySource": json.dumps([{"$match": {"Branch": {"$param": "Branch"}}}, {"$project": {"Name": 1}}]),
    "columns": ["Name"], "formats": ["csv"],
}


class _FakeAdmin:
    def command(self, *_args, **_kwargs):
        return {"ok": 1}


class FakeMongoClient:
    def __init__(self, *_args, **_kwargs):
        self.admin = _FakeAdmin()

    def __getitem__(self, _name):
        return {"orders": object()}

    def close(self):
        pass


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPORTING_ADMIN_TOKEN", ADMIN)
    monkeypatch.delenv("REPORTING_ACCESS_MODE", raising=False)
    monkeypatch.delenv("REPORTING_ACCESS_SECRET", raising=False)
    monkeypatch.delenv("REPORTING_SIGNED_LINK_MAX_SECONDS", raising=False)
    (tmp_path / "connections.json").write_text(json.dumps({"connections": [
        {"id": "default", "name": "Test", "storage": "file", "database": "x"}]}), encoding="utf-8")
    (tmp_path / "private_secrets.json").write_text(json.dumps({
        "admin_token": ADMIN, "mongo_connections": {"default": {"uri": "mongodb://fake/x", "database": "x"}}}), encoding="utf-8")
    report_dir = tmp_path / "reports" / "General"
    report_dir.mkdir(parents=True)
    (report_dir / "SALES.json").write_text(json.dumps(REPORT), encoding="utf-8")
    monkeypatch.setattr(core, "MongoClient", FakeMongoClient)
    monkeypatch.setattr(core, "_execute_report_pipeline", lambda *_a, **_k: [{"Name": "=1+1"}, {"Name": "Ali"}])
    audits = []
    monkeypatch.setattr(core, "_append_report_audit", audits.append)
    client = TestClient(core.make_app())
    client.audits = audits
    return client


def signed(path_and_query: str, secret: str = SECRET) -> str:
    signature = hmac.new(secret.encode(), path_and_query.encode(), hashlib.sha256).hexdigest()
    return f"{path_and_query}&Signature={signature}"


def link(expires_in: int = 300, **params) -> str:
    query = urlencode({"Branch": "LHR", **params, "Expires": int(time.time()) + expires_in})
    return f"/generate/SALES.csv?{query}"


# ---------------------------------------------------------------- no credentials
def test_no_credentials_is_refused_and_hides_report_ids(service):
    assert service.get("/reports").status_code == 401
    assert service.get("/generate/SALES.csv?Branch=LHR").status_code == 401
    unknown = service.get("/generate/DOES_NOT_EXIST.csv")
    assert unknown.status_code == 401  # not 404: existence is not revealed
    assert service.audits == []  # unauthenticated calls do not fill the audit file


def test_open_mode_keeps_old_behaviour(service, monkeypatch):
    monkeypatch.setenv("REPORTING_ACCESS_MODE", "open")
    assert service.get("/reports").status_code == 200
    assert service.get("/generate/SALES.csv?Branch=LHR").status_code == 200


def test_unknown_mode_fails_closed(service, monkeypatch):
    monkeypatch.setenv("REPORTING_ACCESS_MODE", "maybe")
    assert service.get("/reports").status_code == 503


# ---------------------------------------------------------------- header tokens
def test_report_token_header(service, monkeypatch):
    monkeypatch.setenv("REPORTING_ACCESS_SECRET", SECRET)
    assert service.get("/reports", headers={"X-Report-Token": SECRET}).status_code == 200
    response = service.get("/generate/SALES.csv?Branch=LHR", headers={"X-Report-Token": SECRET})
    assert response.status_code == 200
    assert service.audits[-1]["access"] == "report-token"
    assert service.get("/reports", headers={"X-Report-Token": SECRET + "x"}).status_code == 401


def test_short_secret_is_refused(service, monkeypatch):
    monkeypatch.setenv("REPORTING_ACCESS_SECRET", "too-short")
    assert service.get("/reports", headers={"X-Report-Token": "too-short"}).status_code == 503


def test_admin_token_works_without_access_secret(service):
    assert service.get("/reports", headers={"X-Admin-Token": ADMIN}).status_code == 200
    assert service.get("/generate/SALES.csv?Branch=LHR", headers={"X-Admin-Token": ADMIN}).status_code == 200
    assert service.audits[-1]["access"] == "admin-token"


# ---------------------------------------------------------------- signed links
def test_valid_signed_link_downloads_and_is_audited(service, monkeypatch):
    monkeypatch.setenv("REPORTING_ACCESS_SECRET", SECRET)
    response = service.get(signed(link(GeneratedBy="ali")))
    assert response.status_code == 200, response.text
    audit = service.audits[-1]
    assert audit["access"] == "signed-link"
    assert audit["GeneratedBy"] == "ali"
    assert "Signature" not in json.dumps(audit["parameters"]) and "Expires" not in audit["parameters"]


@pytest.mark.parametrize("tamper", [
    lambda url: url.replace("Branch=LHR", "Branch=KHI"),        # edited parameter
    lambda url: url.replace("GeneratedBy=ali", "GeneratedBy=x"),  # forged audit identity
    lambda url: url.replace("SALES.csv", "SALES.pdf"),           # other format
    lambda url: url[:-1] + ("0" if url[-1] != "0" else "1"),     # damaged signature
])
def test_tampered_signed_link_is_refused(service, monkeypatch, tamper):
    monkeypatch.setenv("REPORTING_ACCESS_SECRET", SECRET)
    assert service.get(tamper(signed(link(GeneratedBy="ali")))).status_code == 403


def test_signed_link_with_wrong_secret_is_refused(service, monkeypatch):
    monkeypatch.setenv("REPORTING_ACCESS_SECRET", SECRET)
    assert service.get(signed(link(), secret="x" * 40)).status_code == 403


def test_expired_and_too_long_links_are_refused(service, monkeypatch):
    monkeypatch.setenv("REPORTING_ACCESS_SECRET", SECRET)
    expired = service.get(signed(link(expires_in=-5)))
    assert expired.status_code == 403 and "expire" in expired.json()["detail"]
    too_long = service.get(signed(link(expires_in=7200)))
    assert too_long.status_code == 403
    monkeypatch.setenv("REPORTING_SIGNED_LINK_MAX_SECONDS", "86400")
    assert service.get(signed(link(expires_in=7200))).status_code == 200


def test_signature_must_be_last_and_single_and_expires_required(service, monkeypatch):
    monkeypatch.setenv("REPORTING_ACCESS_SECRET", SECRET)
    good = signed(link())
    path, query = good.split("?", 1)
    parts = query.split("&")
    assert service.get(f"{path}?{'&'.join([parts[-1], *parts[:-1]])}").status_code == 403  # Signature first
    assert service.get(good + "&Signature=abc").status_code == 403                         # two signatures
    no_expiry = signed("/generate/SALES.csv?Branch=LHR")
    assert service.get(no_expiry).status_code == 403


def test_signed_link_needs_secret_on_server(service):
    assert service.get(signed(link())).status_code == 403


def test_reports_listing_does_not_accept_signed_links(service, monkeypatch):
    monkeypatch.setenv("REPORTING_ACCESS_SECRET", SECRET)
    expires = int(time.time()) + 300
    assert service.get(signed(f"/reports?Expires={expires}")).status_code == 401


def test_reserved_parameter_names():
    for name in ("Expires", "Signature"):
        with pytest.raises(ValueError, match="reserved"):
            core._dynamic_report({**REPORT, "parameters": [{"name": name}]})


# ---------------------------------------------------------------- CSV formula prefix
def test_csv_download_prefixes_formula_text(service):
    body = service.get("/generate/SALES.csv?Branch=LHR", headers={"X-Admin-Token": ADMIN}).content.decode("utf-8-sig")
    assert body.splitlines() == ["Name", "'=1+1", "Ali"]


@pytest.mark.parametrize("value, expected", [
    ("=SUM(A1)", "'=SUM(A1)"), ("+1", "'+1"), ("-5", "'-5"), ("@cmd", "'@cmd"), ("\tx", "'\tx"),
    ("Ali", "Ali"), ("", ""), (-5, -5), (3.5, 3.5), (None, None),
])
def test_csv_safe(value, expected):
    assert dr.csv_safe(value) == expected


def test_design_csv_prefixes_formula_text():
    design = dr.validate_design({
        "page": {"width": 595, "height": 842},
        "bands": {
            "columnHeader": {"height": 20, "elements": [{"type": "staticText", "x": 0, "y": 0, "width": 100, "height": 18, "text": "=Header"}]},
            "detail": {"height": 16, "elements": [
                {"type": "textField", "x": 0, "y": 0, "width": 100, "height": 14, "expression": "$F{Name}"},
                {"type": "textField", "x": 120, "y": 0, "width": 100, "height": 14, "expression": "$F{Amount}"},
            ]},
        },
    }, ["Name", "Amount"], set())
    out = dr.render_design_csv(design, "T", ["Name", "Amount"], [{"Name": "+HYPERLINK(1)", "Amount": -5}], {})
    lines = out.decode("utf-8-sig").splitlines()
    assert lines[0].startswith("'=Header")
    assert lines[1] == "'+HYPERLINK(1),-5"  # real number stays a number


# ---------------------------------------------------------------- $P{} collections
def _collection_report(**parameter):
    return {**REPORT, "collection": "Edith-$P{Product}-Plans",
            "parameters": [{"name": "Branch", "type": "string"}, {"name": "Product", "type": "string", **parameter}]}


def test_parameter_collection_requires_allowed_values():
    with pytest.raises(ValueError, match="allowedValues"):
        core._dynamic_report(_collection_report())


def test_parameter_collection_rejects_unsafe_allowed_values():
    with pytest.raises(ValueError, match="allowedValues"):
        core._dynamic_report(_collection_report(allowedValues=["HBL", "x.system"]))


def test_parameter_collection_must_be_defined():
    with pytest.raises(ValueError, match="(?i)define"):
        core._dynamic_report({**REPORT, "collection": "Edith-$P{Missing}"})


def test_parameter_collection_with_allowed_values_is_limited_to_the_list():
    definition = core._dynamic_report(_collection_report(allowedValues=["HBL", "MCB"]))
    product = next(spec for spec in definition.parameters if spec.name == "Product")
    assert core.parse_scalar(product, "HBL") == "HBL"
    with pytest.raises(ValueError, match="allowed"):
        core.parse_scalar(product, "Identity")


def test_jrxml_import_warns_about_collection_parameter():
    import jrxml_import
    from test_jrxml_import import MINI  # noqa: PLC0415 - reuse the existing fixture

    xml = MINI.replace("'collectionName' : $P{CollectionName}", "'collectionName' : 'Plans-$P{ProductCode}'")
    result = jrxml_import.import_jrxml(xml)
    assert result["report"]["collection"] == "Plans-$P{ProductCode}"
    assert any("ProductCode" in warning and "allowedValues" in warning for warning in result["warnings"])
