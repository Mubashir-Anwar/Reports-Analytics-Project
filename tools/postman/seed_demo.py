"""Create the small demo database the Postman collection reads: rs_postman_demo.orders (5 rows).

    python tools/postman/seed_demo.py                       # local MongoDB
    python tools/postman/seed_demo.py mongodb://host:27017  # another server

Only this demo database is touched (its orders collection is replaced).
"""
from __future__ import annotations

import sys

from pymongo import MongoClient

ROWS = [("Ali", "LHR", 1200, "4220112345671"), ("Sara", "KHI", 800, "4210198765432"), ("Omar", "LHR", 450, "3520211122233"),
        ("Hina", "ISB", 2300, "6110144455566"), ("Bilal", "KHI", 990, "4230177788899")]


def main() -> int:
    uri = sys.argv[1] if len(sys.argv) > 1 else "mongodb://127.0.0.1:27017"
    orders = MongoClient(uri, serverSelectionTimeoutMS=5000)["rs_postman_demo"]["orders"]
    orders.delete_many({})
    orders.insert_many([{"Name": name, "Branch": branch, "Amount": amount, "CNIC": cnic} for name, branch, amount, cnic in ROWS])
    print(f"rs_postman_demo.orders: {orders.count_documents({})} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
