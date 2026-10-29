from pymongo import MongoClient

from . import config

client = MongoClient(config.MONGO_URL, serverSelectionTimeoutMS=5000)
db = client[config.MONGO_DB]


def init_db():
    db.leads.create_index("place_id", unique=True)
    db.leads.create_index("phone_e164")
    # Coleta que estava rodando quando o container caiu nunca termina sozinha
    db.runs.update_many(
        {"status": {"$in": ["queued", "running"]}},
        {"$set": {"status": "failed", "error": "reinício da API"}},
    )
