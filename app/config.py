"""Settings, read from environment variables so no secret lives in the code."""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    # Signs the session cookie and the CSRF tokens. If none is set, a random one is
    # made at start-up (everyone is logged out when the app restarts), so the app
    # never runs with a guessable key.
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

    # PostgreSQL: the app logs in as the least-privilege role from db/roles.sql.
    DATABASE_URL = os.environ.get(
        "DATABASE_URL", "postgresql://grievance_app:app-password@localhost/grievance")
    # MongoDB: D8 complaints_raw and D9 ai_proposals (DFD Level 1). The app logs
    # in as grievance_app (readWrite on this one database; db/mongo_users.py).
    MONGO_URL = os.environ.get("MONGO_URL", "mongodb://grievance_app:app-password@127.0.0.1:27017/grievance")
    MONGO_DB = os.environ.get("MONGO_DB", "grievance")

    # D10 attachment files (DFD 2.0). Outside /static, so photos are only
    # reachable through a route that checks who is asking.
    UPLOAD_FOLDER = Path(os.environ.get("UPLOAD_FOLDER", BASE_DIR / "uploads"))
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024          # 5 MB: larger requests get 413
    ALLOWED_PHOTO_TYPES = {"jpg", "jpeg", "png"}

    # Session cookie (Flask security docs)
    SESSION_COOKIE_HTTPONLY = True                 # JavaScript cannot read it
    SESSION_COOKIE_SAMESITE = "Lax"                # not sent on cross-site POSTs
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"  # 1 behind HTTPS

    # A form's CSRF token is valid for 30 minutes; the login cookie itself ends
    # when the browser closes (Flask's default, non-permanent session).
    WTF_CSRF_TIME_LIMIT = 30 * 60

    # Login lockout: this many failures within the window locks the account
    LOGIN_MAX_FAILURES = 5
    LOGIN_LOCK_MINUTES = 15
