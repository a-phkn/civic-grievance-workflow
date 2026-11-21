"""Database connections.

PostgreSQL: one psycopg connection per request, opened on first use and
closed when the request ends. autocommit is on, so a single statement
commits by itself; anything that must be all-or-nothing is wrapped in
`with conn.transaction():`, which commits at the end or rolls back on error.

MongoDB: one MongoClient for the whole app (pymongo keeps its own pool).
"""
import psycopg
from flask import current_app, g
from psycopg.rows import dict_row
from pymongo import ASCENDING, MongoClient

_mongo_client = None


def get_db() -> psycopg.Connection:
    if "db" not in g:
        g.db = psycopg.connect(current_app.config["DATABASE_URL"],
                               autocommit=True, row_factory=dict_row)
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def get_mongo():
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(current_app.config["MONGO_URL"],
                                    serverSelectionTimeoutMS=3000)
    return _mongo_client[current_app.config["MONGO_DB"]]


def init_mongo_indexes():
    """One complaints_raw document per complaint (stops duplicates on retry)."""
    m = get_mongo()
    m.complaints_raw.create_index([("complaint_id", ASCENDING)], unique=True)
    m.ai_proposals.create_index([("complaint_id", ASCENDING)])


def audit(conn, action, *, user_id=None, task_id=None, complaint_id=None,
          old_status=None, new_status=None, reason=None):
    """Write one AUDIT_LOG row (D7). Parameterised like every query here."""
    conn.execute(
        "INSERT INTO audit_log (task_id, complaint_id, user_id, action, old_status, new_status, reason) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (task_id, complaint_id, user_id, action, old_status, new_status, reason))
