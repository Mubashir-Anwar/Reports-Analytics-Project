"""The Query workspace (/query): Mongo browse / find / aggregate / fields, SQL schema assembly, read-only checks."""
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.connectors import sql as sql_connector  # noqa: E402
from backend.app.services import explorer  # noqa: E402


def test_shell_literals_are_parsed_and_writes_refused():
    value = explorer.parse_shell('{ age: { $gt: 5 }, _id: ObjectId("64b7f0c2a1b2c3d4e5f60718"), at: ISODate("2026-01-02") }', "filter", {})
    assert value["age"] == {"$gt": 5}
    assert str(value["_id"]) == "64b7f0c2a1b2c3d4e5f60718"
    assert value["at"] == datetime(2026, 1, 2, tzinfo=timezone.utc)
    assert explorer.parse_shell("  ", "filter", {}) == {}
    with pytest.raises(ValueError, match="JavaScript"):
        explorer.parse_shell('{ $where: "1" }', "filter", {})
    with pytest.raises(ValueError, match="read-only"):
        explorer.mongo_pipeline(['{ $match: {} }', '{ $out: "x" }'])
    with pytest.raises(ValueError, match="Stage 2"):
        explorer.mongo_pipeline(['{ $match: {} }', '{ a: 1, b: 2 }'])
    assert explorer.mongo_pipeline(['{ $match: { a: 1 } }', '', '{ $limit: 3 }']) == [{"$match": {"a": 1}}, {"$limit": 3}]


def test_extended_json_keeps_types():
    from bson import ObjectId
    out = explorer.to_json({"_id": ObjectId("64b7f0c2a1b2c3d4e5f60718"), "at": datetime(2026, 1, 2, tzinfo=timezone.utc)})
    assert out["_id"] == {"$oid": "64b7f0c2a1b2c3d4e5f60718"}
    assert "$date" in out["at"]


def test_sql_schema_is_grouped_with_keys(monkeypatch):
    monkeypatch.setattr(sql_connector, "read_schema", lambda kind, url: {
        "columns": [("public", "orders", "BASE TABLE", "id", "integer", "NO", 1), ("public", "orders", "BASE TABLE", "customer_id", "integer", "YES", 2),
                    ("public", "customers", "BASE TABLE", "id", "integer", "NO", 1), ("public", "big_orders", "VIEW", "id", "integer", "YES", 1)],
        "primaryKeys": [("public", "orders", "id"), ("public", "customers", "id")],
        "foreignKeys": [("public", "orders", "customer_id", "public", "customers", "id")], "truncated": False})
    schema = explorer.sql_schema("postgresql", "postgresql://x/y")
    tables = {t["name"]: t for t in schema["schemas"][0]["tables"]}
    assert tables["big_orders"]["kind"] == "view"
    assert tables["orders"]["columns"][0]["pk"] is True and tables["orders"]["columns"][1]["nullable"] is True
    assert tables["orders"]["columns"][1]["ref"] == "customers.id"
    assert schema["foreignKeys"][0]["refTable"] == "customers"
    assert set(sql_connector.SCHEMA_SQL) == set(sql_connector.DIALECTS)


def test_sql_run_refuses_writes():
    with pytest.raises(ValueError):
        explorer.sql_run("postgresql", "postgresql://x/y", {"sql": "DELETE FROM orders"}, 1000)


MONGO_URL = os.getenv("MONGO_TEST_URL")


@pytest.fixture()
def mongo_db():
    if not MONGO_URL:
        pytest.skip("MONGO_TEST_URL is not set")
    from pymongo import MongoClient
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    name = f"qw_test_{uuid.uuid4().hex[:8]}"
    db = client[name]
    db.orders.insert_many([{"Branch": "KHI" if i % 2 else "LHE", "Amount": i, "Customer": {"Name": f"C{i}"}, "Tags": [{"t": "a"}]} for i in range(30)])
    db.create_collection("orders_view", viewOn="orders", pipeline=[{"$match": {"Branch": "KHI"}}])
    yield db
    client.drop_database(name)
    client.close()


def test_mongo_browse_find_aggregate_fields(mongo_db):
    listing = explorer.mongo_collections(mongo_db)
    collections = {c["name"]: c for c in listing["collections"]}
    assert collections["orders"]["count"] == 30 and collections["orders_view"]["type"] == "view"
    assert listing["total"] == 2 and listing["more"] is False
    first = explorer.mongo_collections(mongo_db, limit=1)                       # one page at a time
    assert [c["name"] for c in first["collections"]] == ["orders"] and first["more"] is True
    assert [c["name"] for c in explorer.mongo_collections(mongo_db, search="VIEW")["collections"]] == ["orders_view"]
    page = explorer.mongo_find(mongo_db, "orders", {"filter": "{ Branch: 'KHI' }", "sort": "{ Amount: -1 }", "projection": "{ Amount: 1 }", "limit": 5, "skip": 1}, 5000)
    assert page["total"] == 15 and [d["Amount"] for d in page["documents"]] == [27, 25, 23, 21, 19]
    assert set(page["documents"][0]) == {"_id", "Amount"} and "$oid" in page["documents"][0]["_id"]
    result = explorer.mongo_aggregate(mongo_db, "orders", {"stages": ["{ $group: { _id: '$Branch', total: { $sum: '$Amount' } } }", "{ $sort: { _id: 1 } }"], "limit": 1}, 5000)
    assert result["documents"] == [{"_id": "KHI", "total": 225}] and result["more"] is True
    fields = {f["path"]: f for f in explorer.mongo_fields(mongo_db, "orders", 5000)["fields"]}
    assert fields["Amount"]["types"] == {"int": 30} and "Customer.Name" in fields and "Tags.t" in fields
    assert explorer.shell_code("orders", [{"$match": {"a": 1}}]).startswith('db.getCollection("orders").aggregate([')
