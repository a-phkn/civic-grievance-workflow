"""
Schema tests for the Civic Grievance Workflow database (Phase 1).

Each test states the rule it checks and where that rule comes from
(ER diagram, relational schema, DFD process or normalization domain).
The database is rebuilt from schema.sql + seed_demo.sql before each group,
so tests do not depend on each other.

Run:  DATABASE_URL=postgresql://user:pass@localhost/grievance python test_schema.py
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
DSN = os.environ.get("DATABASE_URL", "postgresql://dev:dev@localhost/grievance")
results = []


def reset():
    for f in ("schema.sql", "seed_demo.sql"):
        subprocess.run(["psql", DSN, "-q", "-v", "ON_ERROR_STOP=1", "-f", str(HERE / f)],
                       check=True, capture_output=True)


def conn():
    return psycopg.connect(DSN, autocommit=True)


def check(name, source, ok, detail=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  ({source}){'  -- ' + detail if detail else ''}")


def rejected(name, source, sql, params=None):
    """The statement must be refused by the database."""
    with conn() as c:
        try:
            c.execute(sql, params)
            check(name, source, False, "was accepted but should be rejected")
        except psycopg.Error as e:
            check(name, source, True, f"rejected: {e.diag.message_primary}")


def status(c, task_id):
    return c.execute("SELECT status FROM task WHERE task_id = %s", (task_id,)).fetchone()[0]


# ---------------------------------------------------------------- 1. domains and CHECKs
def test_checks():
    reset()
    h = "x" * 20
    rejected("Staff account without an agency", "CHECK agency_id iff AGENCY_STAFF",
             "INSERT INTO app_user (name,email,password_hash,role) VALUES ('a','a@x.in',%s,'AGENCY_STAFF')", (h,))
    rejected("Citizen account with an agency", "CHECK agency_id iff AGENCY_STAFF",
             "INSERT INTO app_user (name,email,password_hash,role,agency_id) VALUES ('a','b@x.in',%s,'CITIZEN',1)", (h,))
    rejected("Unknown role", "domain: role",
             "INSERT INTO app_user (name,email,password_hash,role) VALUES ('a','c@x.in',%s,'SUPERUSER')", (h,))
    rejected("Duplicate email", "schema: email UQ",
             "INSERT INTO app_user (name,email,password_hash,role) VALUES ('a','citizen@civic.demo',%s,'CITIZEN')", (h,))
    rejected("Upper-case email (would dodge UNIQUE)", "CHECK email = lower(email)",
             "INSERT INTO app_user (name,email,password_hash,role) VALUES ('a','Citizen@Civic.demo',%s,'CITIZEN')", (h,))
    rejected("Duplicate agency name", "schema: agency_name UQ",
             "INSERT INTO agency (agency_name) VALUES ('Water')")
    rejected("SLA of 0 hours", "domain: sla_hours > 0",
             "INSERT INTO agency_task_type VALUES (1,'X','x',0)")
    rejected("Complaint filed by a staff user", "trigger T1: citizen_id must be CITIZEN",
             "INSERT INTO complaint (citizen_id, description) VALUES (3,'leak')")
    rejected("Blank complaint description", "CHECK description not blank",
             "INSERT INTO complaint (citizen_id, description) VALUES (2,'   ')")
    rejected("ACCEPTED complaint without a proposal id", "CHECK accepted_proposal_id iff ACCEPTED",
             "INSERT INTO complaint (citizen_id, description, intake_status) VALUES (2,'x','ACCEPTED')")
    rejected("New task created as IN_PROGRESS", "trigger T2a / DFD 4.4",
             "INSERT INTO task (complaint_id,agency_id,task_code,status) VALUES (101,1,'PIPE_REPAIR','IN_PROGRESS')")
    rejected("Task with a task code its agency does not offer", "composite FK TASK -> AGENCY_TASK_TYPE",
             "INSERT INTO task (complaint_id,agency_id,task_code,status) VALUES (101,1,'ROAD_REPAIR','BLOCKED')")
    with conn() as c:   # a second, not yet accepted complaint, so only the CHECK can refuse
        c.execute("INSERT INTO complaint (complaint_id, citizen_id, description) VALUES (102, 2, 'DEMO second')")
        c.execute("INSERT INTO task (task_id, complaint_id, agency_id, task_code, status) "
                  "VALUES (21, 102, 3, 'ROAD_REPAIR', 'BLOCKED')")
    rejected("Task depending on itself", "schema CHECK prerequisite <> dependent",
             "INSERT INTO task_dependency VALUES (21,21,102)")
    rejected("Audit row about a task AND a complaint", "schema CHECK on AUDIT_LOG",
             "INSERT INTO audit_log (task_id, complaint_id, action) VALUES (1,101,'X')")
    rejected("New dependency after the complaint is ACCEPTED", "trigger T4: no dependencies after acceptance",
             "INSERT INTO task_dependency VALUES (1,2,101)")
    with conn() as c:
        rows = c.execute("SELECT task_id, status, ready_at IS NOT NULL, due_at - ready_at FROM task ORDER BY 1").fetchall()
    check("Seed tasks 1 and 2 are READY with deadlines; task 3 BLOCKED without",
          "review gap 2 / CHECK deadlines",
          rows[0][2] and rows[0][3].total_seconds() == 48 * 3600 and
          rows[1][2] and rows[1][3].total_seconds() == 24 * 3600 and
          rows[2][1] == "BLOCKED" and not rows[2][2], str([(r[0], r[1], str(r[3])) for r in rows]))


# ---------------------------------------------------------------- 2. transitions
def test_transitions():
    reset()
    rejected("Illegal jump BLOCKED -> IN_PROGRESS", "trigger T2b, state diagram",
             "UPDATE task SET status='IN_PROGRESS' WHERE task_id=3")
    rejected("Illegal jump READY -> COMPLETED (skips IN_PROGRESS)", "trigger T2b, DFD 5.2",
             "UPDATE task SET status='COMPLETED' WHERE task_id=1")
    rejected("Retry on a task that has not FAILED", "fn_resolve_retry guard, DFD 5.4",
             "SELECT fn_resolve_retry(1, 6, 'try again please')")
    rejected("Cancel a task that is already CANCELLED", "fn_resolve_cancel guard",
             "SELECT fn_resolve_cancel(3, 6, 'not needed') ; SELECT fn_resolve_cancel(3, 6, 'not needed')")
    with conn() as c:
        c.execute("UPDATE task SET status='IN_PROGRESS' WHERE task_id=1")
        c.execute("UPDATE task SET status='COMPLETED' WHERE task_id=1")
        done = c.execute("SELECT completed_at IS NOT NULL FROM task WHERE task_id=1").fetchone()[0]
    check("Legal path READY -> IN_PROGRESS -> COMPLETED stamps completed_at", "T2b + CHECK completed_at", done)
    rejected("COMPLETED is final", "trigger T2b",
             "UPDATE task SET status='IN_PROGRESS' WHERE task_id=1")


# ---------------------------------------------------------------- 3. unlock demo
def test_unlock():
    reset()
    with conn() as c:
        for s in ("IN_PROGRESS", "COMPLETED"):
            c.execute("UPDATE task SET status=%s WHERE task_id=1", (s,))
        check("Task 1 done, task 3 still BLOCKED (task 2 pending)", "DFD 5.3", status(c, 3) == "BLOCKED")
        for s in ("IN_PROGRESS", "COMPLETED"):
            c.execute("UPDATE task SET status=%s WHERE task_id=2", (s,))
        st, sla = c.execute("SELECT status, due_at - ready_at FROM task WHERE task_id=3").fetchone()
        check("Task 2 done -> trigger unlocks task 3 with due_at = ready_at + 72 h", "DFD 5.3",
              st == "READY_FOR_DISPATCH" and sla.total_seconds() == 72 * 3600, f"{st}, {sla}")
        a = c.execute("SELECT count(*) FROM audit_log WHERE task_id=3 AND action='AUTO_UNLOCK' AND user_id IS NULL").fetchone()[0]
        check("Automatic unlock is audited with user_id NULL", "DFD 5.3 -> D7 AUDIT_LOG", a == 1)


# ---------------------------------------------------------------- 4. concurrency
def test_concurrent_unlock():
    """Tasks 1 and 2 complete at the same moment in two transactions.
    Without the FOR UPDATE lock, each would see the other as unfinished
    and task 3 would never unlock."""
    reset()
    with conn() as c:
        c.execute("UPDATE task SET status='IN_PROGRESS' WHERE task_id IN (1,2)")
    a = psycopg.connect(DSN)
    b = psycopg.connect(DSN)
    a.execute("UPDATE task SET status='COMPLETED' WHERE task_id=1")   # A holds the lock on task 3
    done = threading.Event()

    def run_b():
        b.execute("UPDATE task SET status='COMPLETED' WHERE task_id=2")  # waits for A's lock
        b.commit()
        done.set()

    t = threading.Thread(target=run_b)
    t.start()
    time.sleep(1.0)
    waited = not done.is_set()
    a.commit()
    t.join(10)
    a.close()
    b.close()
    with conn() as c:
        st = status(c, 3)
    check("Transaction B waited for A's lock on task 3", "T3 step 2, SELECT ... FOR UPDATE", waited)
    check("Two prerequisites completed concurrently -> task 3 unlocked once", "T3 steps 2-3",
          st == "READY_FOR_DISPATCH", st)


# ---------------------------------------------------------------- 5. exceptions and resolution
def test_exceptions():
    reset()
    with conn() as c:
        c.execute("UPDATE task SET status='IN_PROGRESS' WHERE task_id=1")
        c.execute("UPDATE task SET status='FAILED' WHERE task_id=1")
        check("Task 1 FAILED -> task 3 BLOCKED_EXCEPTION", "DFD 5.3/5.4", status(c, 3) == "BLOCKED_EXCEPTION")
    rejected("Retry without a real reason", "design: every resolution needs a reason",
             "SELECT fn_resolve_retry(1, 6, 'ok')")
    with conn() as c:
        c.execute("SELECT fn_resolve_retry(1, 6, 'Contractor re-assigned')")
        check("Retry: task 1 READY again with new deadlines", "DFD 5.4 retry",
              status(c, 1) == "READY_FOR_DISPATCH")
        check("Retry: task 3 back to BLOCKED (task 2 not done yet)", "DFD 5.4 + re-check",
              status(c, 3) == "BLOCKED")
        n = c.execute("SELECT count(*) FROM audit_log WHERE task_id=1 AND action='RETRY' AND user_id=6").fetchone()[0]
        check("Retry audited with the reviewer and reason", "D7 AUDIT_LOG", n == 1)

    reset()
    with conn() as c:
        c.execute("UPDATE task SET status='IN_PROGRESS' WHERE task_id=1")
        c.execute("UPDATE task SET status='FAILED' WHERE task_id=1")
        c.execute("SELECT fn_resolve_waive(1, 3, 6, 'Road can be fixed before pipe work')")
        check("Waive 1->3: task 3 back to BLOCKED (still waits on task 2)", "DFD 5.4 waive",
              status(c, 3) == "BLOCKED")
        for s in ("IN_PROGRESS", "COMPLETED"):
            c.execute("UPDATE task SET status=%s WHERE task_id=2", (s,))
        check("Task 2 done -> task 3 READY (waived dependency ignored)", "DFD 5.3",
              status(c, 3) == "READY_FOR_DISPATCH")

    reset()
    with conn() as c:
        c.execute("UPDATE task SET status='IN_PROGRESS' WHERE task_id=1")
        c.execute("UPDATE task SET status='FAILED' WHERE task_id=1")
        c.execute("SELECT fn_resolve_cancel(3, 6, 'Road work no longer needed')")
        check("Cancel: BLOCKED_EXCEPTION -> CANCELLED", "DFD 5.4 cancel", status(c, 3) == "CANCELLED")
    with conn() as c:
        c.execute("SELECT fn_resolve_cancel(1, 6, 'Pipe replaced by a private plumber')")
        check("Cancel: FAILED -> CANCELLED (reviewer gives up on a failed task)", "DFD 5.4 cancel",
              status(c, 1) == "CANCELLED")
        for s in ("IN_PROGRESS", "COMPLETED"):
            c.execute("UPDATE task SET status=%s WHERE task_id=2", (s,))
        r = c.execute("SELECT resolved FROM v_complaint_progress WHERE complaint_id=101").fetchone()[0]
        check("Complaint resolves after the failed task is cancelled", "v_complaint_progress", r)
    reset()
    with conn() as c:
        c.execute("UPDATE task SET status='IN_PROGRESS' WHERE task_id=2")
    rejected("Cancel an IN_PROGRESS task (not on the state diagram)", "trigger T2b",
             "SELECT fn_resolve_cancel(2, 6, 'Not needed any more')")


def test_chain():
    """A -> B -> C: an exception travels down the chain, and a retry brings
    the whole chain back."""
    reset()
    with conn() as c:
        c.execute("INSERT INTO complaint (complaint_id, citizen_id, description) VALUES (103, 2, 'DEMO chain')")
        c.execute("INSERT INTO task (task_id, complaint_id, agency_id, task_code, status) VALUES "
                  "(31,103,1,'PIPE_REPAIR','READY_FOR_DISPATCH'),(32,103,2,'SAFETY_CHECK','BLOCKED'),"
                  "(33,103,3,'ROAD_REPAIR','BLOCKED')")
        c.execute("INSERT INTO task_dependency VALUES (31,32,103),(32,33,103)")
        c.execute("UPDATE complaint SET intake_status='ACCEPTED', accepted_proposal_id='000000000000000000000103' "
                  "WHERE complaint_id=103")
        c.execute("UPDATE task SET status='IN_PROGRESS' WHERE task_id=31")
        c.execute("UPDATE task SET status='FAILED' WHERE task_id=31")
        check("A FAILED -> B and C both BLOCKED_EXCEPTION", "T3 step 5, repeated down the chain",
              status(c, 32) == status(c, 33) == "BLOCKED_EXCEPTION")
        c.execute("SELECT fn_resolve_retry(31, 6, 'Second crew assigned')")
        check("Retry A -> B and C both back to BLOCKED", "re-check recurses down the chain",
              status(c, 32) == status(c, 33) == "BLOCKED")


# ---------------------------------------------------------------- 6. transaction rollback (DFD 4.4)
def test_acceptance_rollback():
    reset()
    with psycopg.connect(DSN) as c:
        c.execute("INSERT INTO complaint (complaint_id, citizen_id, description) VALUES (102, 2, 'DEMO second')")
        c.commit()
        try:
            with c.transaction():
                c.execute("INSERT INTO task (task_id, complaint_id, agency_id, task_code, status) "
                          "VALUES (21,102,3,'ROAD_REPAIR','BLOCKED')")
                # bad dependency: prerequisite task 1 belongs to complaint 101
                c.execute("INSERT INTO task_dependency VALUES (1, 21, 102)")
                c.execute("UPDATE complaint SET intake_status='ACCEPTED', "
                          "accepted_proposal_id='000000000000000000000102' WHERE complaint_id=102")
            ok, msg = False, "accepted"
        except psycopg.Error as e:
            ok, msg = True, e.diag.message_primary
        check("Dependency on another complaint's task is rejected", "composite FK TASK_DEPENDENCY -> TASK", ok, msg)
        n = c.execute("SELECT count(*) FROM task WHERE complaint_id=102").fetchone()[0]
        st = c.execute("SELECT intake_status FROM complaint WHERE complaint_id=102").fetchone()[0]
        check("Whole acceptance rolled back (no tasks, still PENDING)", "DFD 4.4 single transaction",
              n == 0 and st == "PENDING", f"tasks={n}, status={st}")


# ---------------------------------------------------------------- 7. views
def test_views():
    reset()
    with conn() as c:
        c.execute("UPDATE task SET ready_at = LOCALTIMESTAMP - interval '3 days', "
                  "due_at = LOCALTIMESTAMP - interval '1 day' WHERE task_id = 1")
        ids = [r[0] for r in c.execute("SELECT task_id FROM v_task_overdue").fetchall()]
        check("Overdue view lists only the late task", "design: overdue computed, not stored", ids == [1], str(ids))
        for t in (1, 2):
            for s in ("IN_PROGRESS", "COMPLETED"):
                c.execute("UPDATE task SET status=%s WHERE task_id=%s", (s, t))
        c.execute("SELECT fn_resolve_cancel(3, 6, 'Road already repaired by contractor')")
        r = c.execute("SELECT resolved FROM v_complaint_progress WHERE complaint_id=101").fetchone()[0]
        check("Complaint resolved when all tasks are COMPLETED or CANCELLED", "design: derived in a view", r)


# ---------------------------------------------------------------- 8. database role (least privilege)
def test_app_role():
    """The app's own login (roles.sql) can do its job but cannot rewrite
    the audit trail or change the schema."""
    reset()
    subprocess.run(["psql", DSN, "-q", "-v", "ON_ERROR_STOP=1", "-v", "app_password='test-app-pw'",
                    "-f", str(HERE / "roles.sql")], check=True, capture_output=True)
    app = psycopg.conninfo.make_conninfo(DSN, user="grievance_app", password="test-app-pw")
    for name, sql in (("App cannot edit an audit entry", "UPDATE audit_log SET reason = 'x'"),
                      ("App cannot delete audit entries", "DELETE FROM audit_log"),
                      ("App cannot delete complaints", "DELETE FROM complaint"),
                      ("App cannot drop a table", "DROP TABLE task")):
        with psycopg.connect(app, autocommit=True) as c:
            try:
                c.execute(sql)
                check(name, "roles.sql least privilege", False, "was allowed")
            except psycopg.Error as e:
                check(name, "roles.sql least privilege", True, e.diag.message_primary)
    with psycopg.connect(app, autocommit=True) as c:
        c.execute("UPDATE task SET status='IN_PROGRESS' WHERE task_id=1")
        check("App can still update a task status", "roles.sql least privilege", status(c, 1) == "IN_PROGRESS")


if __name__ == "__main__":
    for t in (test_checks, test_transitions, test_unlock, test_concurrent_unlock,
              test_exceptions, test_chain, test_acceptance_rollback, test_views, test_app_role):
        print(f"\n== {t.__name__} ==")
        t()
    reset()  # leave the demo data in its starting state
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)
