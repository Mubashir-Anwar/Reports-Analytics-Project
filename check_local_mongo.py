from datetime import datetime, timezone

from pymongo import MongoClient

client = MongoClient(
    "mongodb://localhost:27017/?connectTimeoutMS=10000",
    serverSelectionTimeoutMS=5000,
)
db = client["cas-cade"]
collection = db["Edith-KMBLGL-Workflows"]

print("PING:", client.admin.command("ping")["ok"])
print("TOTAL:", collection.count_documents({}))

pipeline = [
    {"$addFields": {"LastUpdateDateTime": {"$toDate": "$LastUpdateDateTime"}}},
    {"$match": {"LastUpdateDateTime": {
        "$gte": datetime(2025, 10, 27, tzinfo=timezone.utc),
        "$lte": datetime(2099, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc),
    }}},
    {"$count": "dateMatched"},
]
print("DATE MATCH:", list(collection.aggregate(pipeline)))
client.close()
