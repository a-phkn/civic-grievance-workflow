"""Create the MongoDB logins (run once, BEFORE turning on authorization).

grievance_admin : manages users only (userAdminAnyDatabase), for later changes.
grievance_app   : what the Flask app and seed_mongo.py use. readWrite on the
                  'grievance' database only, so it cannot see or change any
                  other database or create users. (Least privilege, like
                  db/roles.sql for PostgreSQL.)

Authorization is still off at this point, so no login is needed to create
the users (MongoDB manual, "Enable Access Control": create the user
administrator first, then restart with access control on).

Run:  MONGO_ADMIN_PASSWORD=... MONGO_APP_PASSWORD=app-password python db/mongo_users.py
Then add  security: authorization: enabled  to mongod.conf and restart MongoDB.
"""
import os

from pymongo import MongoClient

client = MongoClient(os.environ.get("MONGO_ADMIN_URL", "mongodb://127.0.0.1:27017"))
admin_pw = os.environ["MONGO_ADMIN_PASSWORD"]
app_pw = os.environ.get("MONGO_APP_PASSWORD", "app-password")
db_name = os.environ.get("MONGO_DB", "grievance")

admin = client.admin
if not admin.command("usersInfo", "grievance_admin")["users"]:
    admin.command("createUser", "grievance_admin", pwd=admin_pw,
                  roles=[{"role": "userAdminAnyDatabase", "db": "admin"}])
    print("created grievance_admin")

app_db = client[db_name]
if not app_db.command("usersInfo", "grievance_app")["users"]:
    app_db.command("createUser", "grievance_app", pwd=app_pw,
                   roles=[{"role": "readWrite", "db": db_name}])
    print(f"created grievance_app (readWrite on {db_name} only)")
