"""End-to-end tests of the Flask app against real PostgreSQL and MongoDB.

Each test names the rubric point or DFD process it proves. CSRF protection
stays ON in these tests, so every POST fetches a real token first, exactly
like a browser.

Run:  pytest -v tests/test_app.py
Env:  OWNER_DATABASE_URL (to rebuild the schema), DATABASE_URL (app role),
      MONGO_URL
"""
import io
import os
import re
import subprocess
from pathlib import Path

import psycopg
import pytest
from pymongo import MongoClient
from pymongo.errors import OperationFailure

ROOT = Path(__file__).resolve().parent.parent
OWNER_DSN = os.environ.get("OWNER_DATABASE_URL", "postgresql://dev:dev@localhost/grievance")
APP_DSN = os.environ.get("DATABASE_URL", "postgresql://grievance_app:app-password@localhost/grievance")
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://grievance_app:app-password@127.0.0.1:27017/grievance")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PW = "Demo@2026"


def reset_databases():
    for f, extra in (("schema.sql", []), ("seed_demo.sql", []),
                     ("roles.sql", ["-v", "app_password='app-password'"])):
        subprocess.run(["psql", OWNER_DSN, "-q", "-v", "ON_ERROR_STOP=1", *extra, "-f", str(ROOT / "db" / f)],
                       check=True, capture_output=True)
    subprocess.run(["python", str(ROOT / "db" / "seed_mongo.py")], check=True, capture_output=True,
                   env={**os.environ, "MONGO_URL": MONGO_URL})


@pytest.fixture()
def app(tmp_path):
    reset_databases()
    from app import create_app
    return create_app({"SECRET_KEY": "test-secret", "DATABASE_URL": APP_DSN, "MONGO_URL": MONGO_URL,
                       "UPLOAD_FOLDER": tmp_path})


@pytest.fixture()
def client(app):
    return app.test_client()


def sql(query, params=None):
    with psycopg.connect(OWNER_DSN, autocommit=True) as c:
        return c.execute(query, params).fetchall()


def token(client, url):
    page = client.get(url).get_data(as_text=True)
    m = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', page) or \
        re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert m, f"no CSRF token on {url}"
    return m.group(1)


def login(client, email, password=PW):
    t = token(client, "/login")
    return client.post("/login", data={"csrf_token": t, "email": email, "password": password})


def mongo():
    return MongoClient(MONGO_URL)["grievance"]


# ------------------------------------------------------------------ access control (role-wise access)
ROLE_PAGES = {
    "citizen@civic.demo": "/citizen/complaints",
    "water.staff@civic.demo": "/staff/tasks",
    "reviewer@civic.demo": "/reviewer/complaints",
    "admin@civic.demo": "/admin/",
}


@pytest.mark.parametrize("email", ROLE_PAGES)
def test_each_role_sees_only_its_own_pages(client, email):
    """Role-wise access: own page 200, every other role's page 403 (and audited)."""
    assert login(client, email).status_code == 302
    for other, page in ROLE_PAGES.items():
        code = client.get(page).status_code
        assert code == (200 if other == email else 403), (email, page, code)
    uid = sql("SELECT user_id FROM app_user WHERE email = %s", (email,))[0][0]
    assert sql("SELECT count(*) FROM audit_log WHERE user_id = %s AND action = 'ACCESS_DENIED'", (uid,))[0][0] == 3


def test_anonymous_is_sent_to_login(client):
    r = client.get("/reviewer/complaints")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_citizen_cannot_open_another_citizens_complaint(client):
    """Scope 'own complaints only': complaint 101 belongs to the demo citizen."""
    t = token(client, "/register")
    client.post("/register", data={"csrf_token": t, "name": "Other Person", "email": "other@civic.demo",
                                   "password": "abcd1234", "confirm": "abcd1234"})
    login(client, "other@civic.demo", "abcd1234")
    assert client.get("/citizen/complaints/101").status_code == 404


def test_staff_cannot_update_another_agencys_task(client):
    """DFD 5.2 scope: Water staff -> Electricity task 2 is refused and audited."""
    login(client, "water.staff@civic.demo")
    t = token(client, "/staff/tasks")
    r = client.post("/staff/tasks/2/status", data={"t2-csrf_token": t, "csrf_token": t,
                                                   "t2-new_status": "IN_PROGRESS"})
    assert r.status_code == 403
    assert sql("SELECT status FROM task WHERE task_id = 2")[0][0] == "READY_FOR_DISPATCH"
    assert sql("SELECT count(*) FROM audit_log WHERE action = 'ACCESS_DENIED' AND reason LIKE '%task 2%'")[0][0] == 1


def test_register_cannot_choose_a_role(client):
    """A forged 'role=ADMIN' field is ignored: self-registration is always CITIZEN."""
    t = token(client, "/register")
    client.post("/register", data={"csrf_token": t, "name": "Sneaky", "email": "sneaky@civic.demo",
                                   "password": "abcd1234", "confirm": "abcd1234", "role": "ADMIN"})
    assert sql("SELECT role FROM app_user WHERE email = 'sneaky@civic.demo'")[0][0] == "CITIZEN"


# ------------------------------------------------------------------ security
def test_post_without_csrf_token_is_rejected(client):
    login(client, "citizen@civic.demo")
    r = client.post("/citizen/complaints/new", data={"description": "x" * 30})
    assert r.status_code == 400 and "security token" in r.get_data(as_text=True)
    assert sql("SELECT count(*) FROM complaint")[0][0] == 1


def test_sql_injection_in_login_fails(client):
    r = login(client, "admin@civic.demo' OR '1'='1", "' OR '1'='1")
    assert r.status_code in (200, 401) and b"Log out" not in r.data


def test_sql_injection_text_is_stored_as_plain_text(client):
    login(client, "citizen@civic.demo")
    evil = "Pothole'); DROP TABLE complaint; -- near the market road"
    t = token(client, "/citizen/complaints/new")
    client.post("/citizen/complaints/new", data={"csrf_token": t, "description": evil})
    assert sql("SELECT description FROM complaint ORDER BY complaint_id DESC LIMIT 1")[0][0] == evil


def test_script_in_complaint_is_escaped(client):
    """XSS: Jinja autoescaping shows the text, never runs it."""
    login(client, "citizen@civic.demo")
    t = token(client, "/citizen/complaints/new")
    r = client.post("/citizen/complaints/new", data={"csrf_token": t,
                    "description": "<script>alert('x')</script> broken streetlight here"}, follow_redirects=True)
    body = r.get_data(as_text=True)
    assert "<script>alert" not in body and "&lt;script&gt;" in body


def test_passwords_are_argon2id_and_never_plain(client):
    t = token(client, "/register")
    client.post("/register", data={"csrf_token": t, "name": "New Citizen", "email": "new@civic.demo",
                                   "password": "abcd1234", "confirm": "abcd1234"})
    h = sql("SELECT password_hash FROM app_user WHERE email = 'new@civic.demo'")[0][0]
    assert h.startswith("$argon2id$") and "abcd1234" not in h


def test_login_locks_after_five_failures(client):
    for _ in range(5):
        assert login(client, "citizen@civic.demo", "wrong-pass1").status_code == 401
    r = login(client, "citizen@civic.demo")          # right password, but locked
    assert r.status_code == 429


def test_unknown_email_and_wrong_password_look_the_same(client):
    a = login(client, "nobody@civic.demo", "whatever1").get_data(as_text=True)
    b = login(client, "citizen@civic.demo", "whatever1").get_data(as_text=True)
    assert "Incorrect email or password." in a and "Incorrect email or password." in b


def test_next_parameter_is_never_followed(client):
    t = token(client, "/login")
    r = client.post("/login?next=//evil.example.com/x", data={"csrf_token": t, "email": "citizen@civic.demo",
                                                              "password": PW})
    assert r.headers["Location"] == "/citizen/complaints"


def test_security_headers(client):
    h = client.get("/login").headers
    assert "default-src 'self'" in h["Content-Security-Policy"]
    assert h["X-Content-Type-Options"] == "nosniff" and h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "same-origin"
    r = login(client, "citizen@civic.demo")
    cookie = r.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert client.get("/citizen/complaints").headers["Cache-Control"] == "no-store"


def test_mongodb_needs_a_login_and_app_user_is_limited(client):
    """MongoDB runs with authorization on; grievance_app can use only its own database."""
    with pytest.raises(OperationFailure):
        MongoClient("mongodb://127.0.0.1:27017")["grievance"].list_collection_names()
    with pytest.raises(OperationFailure):
        MongoClient(MONGO_URL)["admin"].list_collection_names()
    assert "complaints_raw" in mongo().list_collection_names()


def test_idle_session_expires(client):
    """OWASP session management: an idle login expires (30 min; 1 s here to test it)."""
    import time
    from datetime import timedelta
    client.application.permanent_session_lifetime = timedelta(seconds=3)
    r = login(client, "citizen@civic.demo")
    assert "Expires=" in r.headers["Set-Cookie"]           # a timed cookie, not "until browser closes"
    for _ in range(3):                                      # active use keeps the login alive (idle, not absolute)
        time.sleep(1.5)
        assert client.get("/citizen/complaints").status_code == 200
    time.sleep(4)                                           # idle longer than the limit
    r = client.get("/citizen/complaints")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_logout_requires_post(client):
    login(client, "citizen@civic.demo")
    assert client.get("/logout").status_code == 405


# ------------------------------------------------------------------ validation messages
def test_citizen_sees_why_complaint_was_rejected(client):
    """Rationale explanation: the reviewer's reason (stored in AUDIT_LOG) is shown
    to the citizen who owns the complaint."""
    login(client, "citizen@civic.demo")
    t = token(client, "/citizen/complaints/new")
    client.post("/citizen/complaints/new", data={"csrf_token": t,
                "description": "Dog barking every night in the next building"})
    cid = sql("SELECT max(complaint_id) FROM complaint")[0][0]
    client.post("/logout", data={"csrf_token": token(client, "/citizen/complaints")})
    login(client, "reviewer@civic.demo")
    t = token(client, f"/reviewer/complaints/{cid}")
    reason = "Noise from neighbours is handled by the police helpline, not civic agencies"
    client.post(f"/reviewer/complaints/{cid}/reject", data={"reject-csrf_token": t, "reject-reason": reason})
    assert sql("SELECT intake_status FROM complaint WHERE complaint_id = %s", (cid,))[0][0] == "REJECTED"
    client.post("/logout", data={"csrf_token": token(client, "/reviewer/complaints")})
    login(client, "citizen@civic.demo")
    page = client.get(f"/citizen/complaints/{cid}").get_data(as_text=True)
    assert "not accepted" in page and reason in page


def test_register_validation_messages(client):
    t = token(client, "/register")
    r = client.post("/register", data={"csrf_token": t, "name": "1", "email": "not-an-email",
                                       "password": "short", "confirm": "different"})
    body = r.get_data(as_text=True)
    for msg in ("Enter a valid email address.", "Use 8 to 128 characters.",
                "The two passwords do not match.", "Please fix"):
        assert msg in body, msg


def test_duplicate_email_message(client):
    t = token(client, "/register")
    r = client.post("/register", data={"csrf_token": t, "name": "Copy Cat", "email": "Citizen@Civic.demo",
                                       "password": "abcd1234", "confirm": "abcd1234"})
    assert r.status_code == 409 and "already exists" in r.get_data(as_text=True)


def test_complaint_too_short(client):
    login(client, "citizen@civic.demo")
    t = token(client, "/citizen/complaints/new")
    r = client.post("/citizen/complaints/new", data={"csrf_token": t, "description": "leak"})
    assert "Use 20 to 2,000 characters." in r.get_data(as_text=True)


def test_admin_sla_must_be_positive(client):
    login(client, "admin@civic.demo")
    t = token(client, "/admin/")
    r = client.post("/admin/task-types", data={"tt-csrf_token": t, "tt-agency_id": "1", "tt-task_code": "DRAIN",
                                               "tt-sla_hours": "0"})
    assert "Use a whole number from 1 to 8760" in r.get_data(as_text=True)
    r = client.post("/admin/task-types", data={"tt-csrf_token": t, "tt-agency_id": "1",
                                               "tt-task_code": "bad code!", "tt-sla_hours": "5"})
    assert "Capital letters, digits and _ only" in r.get_data(as_text=True)


def test_admin_staff_needs_agency(client):
    login(client, "admin@civic.demo")
    t = token(client, "/admin/")
    r = client.post("/admin/users", data={"user-csrf_token": t, "user-name": "New Staff",
                                          "user-email": "ns@civic.demo", "user-role": "AGENCY_STAFF",
                                          "user-agency_id": "0", "user-password": "abcd1234"})
    assert "Agency staff must belong to an agency." in r.get_data(as_text=True)


# ------------------------------------------------------------------ uploads (D10)
def test_photo_upload_accepted_and_private(client, app):
    login(client, "citizen@civic.demo")
    t = token(client, "/citizen/complaints/new")
    r = client.post("/citizen/complaints/new", content_type="multipart/form-data",
                    data={"csrf_token": t, "description": "Overflowing drain on 5th Cross road",
                          "photo": (io.BytesIO(PNG), "../../etc/drain.png")})
    assert r.status_code == 302
    cid = sql("SELECT max(complaint_id) FROM complaint")[0][0]
    doc = mongo().complaints_raw.find_one({"complaint_id": cid})
    name = doc["attachments"][0]
    assert re.fullmatch(r"[0-9a-f]{32}\.png", name)          # random name, user's path ignored
    assert client.get(f"/citizen/photo/{cid}/{name}").status_code == 200
    client.post("/logout", data={"csrf_token": token(client, "/citizen/complaints")})
    login(client, "water.staff@civic.demo")
    assert client.get(f"/citizen/photo/{cid}/{name}").status_code == 403


def test_renamed_non_image_is_rejected(client):
    login(client, "citizen@civic.demo")
    t = token(client, "/citizen/complaints/new")
    r = client.post("/citizen/complaints/new", content_type="multipart/form-data",
                    data={"csrf_token": t, "description": "Street light broken near the park gate",
                          "photo": (io.BytesIO(b"<?php echo 'hi'; ?>"), "photo.png")})
    assert "not a real JPG or PNG" in r.get_data(as_text=True)
    assert sql("SELECT count(*) FROM complaint")[0][0] == 1


def test_wrong_extension_is_rejected(client):
    login(client, "citizen@civic.demo")
    t = token(client, "/citizen/complaints/new")
    r = client.post("/citizen/complaints/new", content_type="multipart/form-data",
                    data={"csrf_token": t, "description": "Street light broken near the park gate",
                          "photo": (io.BytesIO(b"x"), "page.html")})
    assert "Only JPG or PNG images are accepted." in r.get_data(as_text=True)


def test_upload_over_5mb_is_refused(client):
    login(client, "citizen@civic.demo")
    t = token(client, "/citizen/complaints/new")
    r = client.post("/citizen/complaints/new", content_type="multipart/form-data",
                    data={"csrf_token": t, "description": "Huge photo of a pothole on the main road",
                          "photo": (io.BytesIO(PNG + b"0" * (6 * 1024 * 1024)), "big.png")})
    assert r.status_code == 413


# ------------------------------------------------------------------ DFD 2.0: both databases
def test_complaint_written_to_postgres_and_mongodb(client):
    login(client, "citizen@civic.demo")
    t = token(client, "/citizen/complaints/new")
    text = "Garbage not collected for a week at 4th Block"
    client.post("/citizen/complaints/new", data={"csrf_token": t, "description": text})
    cid, status = sql("SELECT complaint_id, intake_status FROM complaint ORDER BY complaint_id DESC LIMIT 1")[0]
    doc = mongo().complaints_raw.find_one({"complaint_id": cid})
    assert status == "PENDING" and doc["original_text"] == text
    assert "citizen" not in str(doc).lower() and "@" not in str(doc)   # no name or email in MongoDB


def test_mongodb_failure_rolls_back_postgres(client, monkeypatch):
    """Reconciled Design, cross-database rule: MongoDB down -> no half-saved complaint."""
    from pymongo.collection import Collection

    def boom(*a, **k):
        raise RuntimeError("MongoDB unavailable")
    monkeypatch.setattr(Collection, "insert_one", boom)
    login(client, "citizen@civic.demo")
    t = token(client, "/citizen/complaints/new")
    r = client.post("/citizen/complaints/new", data={"csrf_token": t,
                    "description": "Broken footpath tiles near the school gate"})
    assert r.status_code == 503 and "could not be saved" in r.get_data(as_text=True)
    assert sql("SELECT count(*) FROM complaint")[0][0] == 1          # only the seed complaint


# ------------------------------------------------------------------ DFD 5: the workflow through the UI
def staff_move(client, task_id, new_status):
    t = token(client, "/staff/tasks")
    return client.post(f"/staff/tasks/{task_id}/status",
                       data={f"t{task_id}-csrf_token": t, f"t{task_id}-new_status": new_status})


def test_unlock_demo_through_the_ui(client):
    """Handoff demo: complete task 1 -> task 3 still BLOCKED; complete task 2 -> task 3 unlocked, due +72 h."""
    login(client, "water.staff@civic.demo")
    staff_move(client, 1, "IN_PROGRESS")
    staff_move(client, 1, "COMPLETED")
    assert sql("SELECT status FROM task WHERE task_id = 3")[0][0] == "BLOCKED"
    client.post("/logout", data={"csrf_token": token(client, "/staff/tasks")})
    login(client, "power.staff@civic.demo")
    staff_move(client, 2, "IN_PROGRESS")
    staff_move(client, 2, "COMPLETED")
    status, hours = sql("SELECT status, extract(epoch FROM due_at - ready_at) / 3600 FROM task WHERE task_id = 3")[0]
    assert status == "READY_FOR_DISPATCH" and hours == 72
    assert "ROAD_REPAIR" not in client.get("/staff/tasks").get_data(as_text=True)   # Roads task not shown to Power
    client.post("/logout", data={"csrf_token": token(client, "/staff/tasks")})
    login(client, "roads.staff@civic.demo")
    assert "ROAD_REPAIR" in client.get("/staff/tasks").get_data(as_text=True)


def test_waiting_step_names_what_it_waits_for(client):
    """Rationale explanation: a blocked step names the earlier steps it still waits
    for; a step that is done drops off the list. Reviewer sees the same per task."""
    login(client, "water.staff@civic.demo")
    staff_move(client, 1, "IN_PROGRESS")
    staff_move(client, 1, "COMPLETED")
    client.post("/logout", data={"csrf_token": token(client, "/staff/tasks")})
    login(client, "citizen@civic.demo")
    page = client.get("/citizen/complaints/101").get_data(as_text=True)
    waiting = re.search(r"Waiting for: (.*?)</p>", page).group(1)
    assert "Electricity: Make exposed or damaged power lines safe (Ready for the agency)" in waiting
    assert "Water" not in waiting                            # completed, so no longer waited for
    client.post("/logout", data={"csrf_token": token(client, "/citizen/complaints")})
    login(client, "reviewer@civic.demo")
    page = client.get("/reviewer/complaints/101").get_data(as_text=True)
    assert "#1 PIPE_REPAIR (Completed), #2" in page


def test_history_shows_cause_before_effect(client):
    """Audit order: the staff member's completion is logged before the unlock
    it caused (both rows share one transaction timestamp, so audit_id decides)."""
    login(client, "water.staff@civic.demo")
    staff_move(client, 1, "IN_PROGRESS")
    staff_move(client, 1, "COMPLETED")
    client.post("/logout", data={"csrf_token": token(client, "/staff/tasks")})
    login(client, "power.staff@civic.demo")
    staff_move(client, 2, "IN_PROGRESS")
    staff_move(client, 2, "COMPLETED")
    rows = sql("SELECT action, task_id, new_status FROM audit_log WHERE task_id IN (2, 3) "
               "ORDER BY created_at, audit_id")
    assert rows.index(("STATUS_CHANGE", 2, "COMPLETED")) < rows.index(("AUTO_UNLOCK", 3, "READY_FOR_DISPATCH")), rows


def test_staff_cannot_skip_a_step(client):
    """A forged POST of READY -> COMPLETED is refused (only legal next steps)."""
    login(client, "water.staff@civic.demo")
    staff_move(client, 1, "COMPLETED")
    assert sql("SELECT status FROM task WHERE task_id = 1")[0][0] == "READY_FOR_DISPATCH"


def test_stale_status_change_is_refused(client):
    """Staff loaded the page while task 1 was IN_PROGRESS; someone else completed it
    before they pressed "Report a failure". The request re-reads the status, finds
    FAILED is not a legal step from COMPLETED, and refuses: the completion stands."""
    login(client, "water.staff@civic.demo")
    staff_move(client, 1, "IN_PROGRESS")
    t = token(client, "/staff/tasks")                       # page shows IN_PROGRESS
    sql("UPDATE task SET status = 'COMPLETED' WHERE task_id = 1 RETURNING task_id")  # the other person
    r = client.post("/staff/tasks/1/status", data={"t1-csrf_token": t, "t1-new_status": "FAILED"},
                    follow_redirects=True)
    assert "not allowed from COMPLETED" in r.get_data(as_text=True)
    assert sql("SELECT status FROM task WHERE task_id = 1")[0][0] == "COMPLETED"
    assert sql("SELECT count(*) FROM audit_log WHERE task_id = 1 AND new_status = 'FAILED'")[0][0] == 0


def test_failure_then_reviewer_retry(client):
    login(client, "water.staff@civic.demo")
    staff_move(client, 1, "IN_PROGRESS")
    staff_move(client, 1, "FAILED")
    assert sql("SELECT status FROM task WHERE task_id = 3")[0][0] == "BLOCKED_EXCEPTION"
    client.post("/logout", data={"csrf_token": token(client, "/staff/tasks")})
    login(client, "reviewer@civic.demo")
    t = token(client, "/reviewer/exceptions")
    r = client.post("/reviewer/tasks/1/retry", data={"csrf_token": t, "reason": "ok"}, follow_redirects=True)
    assert "needs a reason" in r.get_data(as_text=True)
    client.post("/reviewer/tasks/1/retry", data={"csrf_token": t, "reason": "New contractor assigned"})
    assert [r[0] for r in sql("SELECT status FROM task WHERE task_id IN (1, 3) ORDER BY task_id")] == \
        ["READY_FOR_DISPATCH", "BLOCKED"]
    assert sql("SELECT count(*) FROM audit_log WHERE action = 'RETRY' AND reason = 'New contractor assigned'")[0][0] == 1


def test_reviewer_waive_and_cancel(client):
    login(client, "water.staff@civic.demo")
    staff_move(client, 1, "IN_PROGRESS")
    staff_move(client, 1, "FAILED")
    client.post("/logout", data={"csrf_token": token(client, "/staff/tasks")})
    login(client, "reviewer@civic.demo")
    t = token(client, "/reviewer/exceptions")
    client.post("/reviewer/dependencies/1/3/waive", data={"csrf_token": t, "reason": "Road fix can go first"})
    assert sql("SELECT status FROM task WHERE task_id = 3")[0][0] == "BLOCKED"
    client.post("/reviewer/tasks/1/cancel", data={"csrf_token": t, "reason": "Pipe replaced privately"})
    assert sql("SELECT status FROM task WHERE task_id = 1")[0][0] == "CANCELLED"


def test_admin_sla_change_is_audited(client):
    login(client, "admin@civic.demo")
    t = token(client, "/admin/")
    client.post("/admin/task-types/1/PIPE_REPAIR/sla", data={"sla-csrf_token": t, "sla-sla_hours": "36"})
    assert sql("SELECT sla_hours FROM agency_task_type WHERE task_code = 'PIPE_REPAIR'")[0][0] == 36
    assert sql("SELECT count(*) FROM audit_log WHERE action = 'ADMIN_CHANGE' "
               "AND reason = 'PIPE_REPAIR SLA 48 → 36 h' AND task_id IS NULL AND complaint_id IS NULL")[0][0] == 1


def test_admin_creates_staff_who_can_log_in(client):
    login(client, "admin@civic.demo")
    t = token(client, "/admin/")
    client.post("/admin/users", data={"user-csrf_token": t, "user-name": "Second Water", "user-email": "w2@civic.demo",
                                      "user-role": "AGENCY_STAFF", "user-agency_id": "1", "user-password": "abcd1234"})
    client.post("/logout", data={"csrf_token": token(client, "/admin/")})
    assert login(client, "w2@civic.demo", "abcd1234").headers["Location"] == "/staff/tasks"
