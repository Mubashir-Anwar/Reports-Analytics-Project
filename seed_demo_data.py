"""Insert a few clearly synthetic rows into a dedicated local demo collection."""

import os
from datetime import datetime, timezone
from pathlib import Path

from pymongo import MongoClient


def load_local_env_file() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


load_local_env_file()

URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DATABASE = os.getenv("MONGODB_DATABASE", "reporting_demo")
COLLECTION = os.getenv("SBP_COLLECTION", "report_rows_demo")

# Refuse to seed a collection that does not clearly identify itself as a demo.
if not COLLECTION.lower().endswith("_demo"):
    raise SystemExit("Safety stop: the SBP_COLLECTION name must end with _demo.")

SAMPLE_ROWS = [
    {
        "_id": "demo-sbp-001",
        "transaction_date": datetime(2025, 11, 1, 12, 0, tzinfo=timezone.utc),
        "instref": "DEMO-001",
        "branch_code": "0157",
        "occupation": "S",
        "secured": 1,
        "amount": 12500.00,
    },
    {
        "_id": "demo-sbp-002",
        "transaction_date": datetime(2025, 12, 15, 9, 30, tzinfo=timezone.utc),
        "instref": "DEMO-002",
        "branch_code": "0157",
        "occupation": "S",
        "secured": 0,
        "amount": 7800.00,
    },
    {
        "_id": "demo-sbp-003",
        "transaction_date": datetime(2026, 1, 10, 16, 45, tzinfo=timezone.utc),
        "instref": "DEMO-003",
        "branch_code": "0201",
        "occupation": "B",
        "secured": 1,
        "amount": 22000.00,
    },
]


def main() -> None:
    client = MongoClient(URI, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
        collection = client[DATABASE][COLLECTION]
        for row in SAMPLE_ROWS:
            collection.update_one({"_id": row["_id"]}, {"$set": row}, upsert=True)
        print(f"{len(SAMPLE_ROWS)} synthetic rows upserted into {DATABASE}.{COLLECTION}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
