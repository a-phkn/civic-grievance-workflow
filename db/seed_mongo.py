"""Demo documents for MongoDB, matching seed_demo.sql (complaint 101).

complaints_raw (D8): the citizen's original text for complaint 101.
ai_proposals  (D9): the proposal that complaint 101's workflow came from.
Its _id is the demo ObjectId stored in COMPLAINT.accepted_proposal_id.

Run after seed_demo.sql:  python seed_mongo.py   (MONGO_URL if not the default)
"""
import os
from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ASCENDING, MongoClient

db = MongoClient(os.environ.get("MONGO_URL", "mongodb://grievance_app:app-password@127.0.0.1:27017/grievance"))[os.environ.get("MONGO_DB", "grievance")]
db.complaints_raw.drop()
db.ai_proposals.drop()
db.complaints_raw.create_index([("complaint_id", ASCENDING)], unique=True)
db.ai_proposals.create_index([("complaint_id", ASCENDING)])

db.complaints_raw.insert_one({
    "complaint_id": 101,
    "original_text": "DEMO: Burst water pipe has flooded the road outside 12th Main, "
                     "and a street-light cable is lying in the water.",
    "attachments": [],
    "submitted_at": datetime.now(timezone.utc),
})
db.ai_proposals.insert_one({
    "_id": ObjectId("000000000000000000000101"),
    "complaint_id": 101,
    "model": "DEMO (no AI in Phase 1)",
    "prompt_version": "demo",
    "proposed_tasks": [
        {"label": "T1", "agency": "Water", "task_code": "PIPE_REPAIR", "confidence": 1.0},
        {"label": "T2", "agency": "Electricity", "task_code": "SAFETY_CHECK", "confidence": 1.0},
        {"label": "T3", "agency": "Roads", "task_code": "ROAD_REPAIR", "confidence": 1.0},
    ],
    "proposed_dependencies": [{"before": "T1", "after": "T3"}, {"before": "T2", "after": "T3"}],
    "validation_status": "ACCEPTED",
    "validation_errors": [],
})
print("MongoDB demo data loaded: complaints_raw 1, ai_proposals 1")
