"""REVIEWER: DFD 4.3 review decision (reject), DFD 5.4 Handle Exception
(retry / waive / cancel), DFD 6.0 reports. Scope: all complaints.

Phase 1 note: AI proposals (DFD 3.0) and acceptance (4.1, 4.2, 4.4) come in
a later phase; the demo complaint 101 is loaded already accepted."""
import psycopg
from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user

from .auth import role_required
from .db import audit, get_db, get_mongo
from .forms import ReasonForm

bp = Blueprint("reviewer", __name__, url_prefix="/reviewer")


@bp.route("/complaints")
@role_required("REVIEWER")
def complaints():
    rows = get_db().execute(
        "SELECT p.*, c.created_at, left(c.description, 90) AS summary, u.name AS citizen "
        "FROM v_complaint_progress p JOIN complaint c USING (complaint_id) "
        "JOIN app_user u ON u.user_id = c.citizen_id ORDER BY c.created_at DESC").fetchall()
    return render_template("reviewer/complaints.html", rows=rows)


@bp.route("/complaints/<int:complaint_id>")
@role_required("REVIEWER")
def complaint_detail(complaint_id):
    conn = get_db()
    c = conn.execute("SELECT c.*, u.name AS citizen FROM complaint c "
                     "JOIN app_user u ON u.user_id = c.citizen_id WHERE complaint_id = %s",
                     (complaint_id,)).fetchone()
    if c is None:
        abort(404)
    tasks = conn.execute(
        "SELECT t.*, a.agency_name FROM task t JOIN agency a USING (agency_id) "
        "WHERE complaint_id = %s ORDER BY task_id", (complaint_id,)).fetchall()
    deps = conn.execute("SELECT * FROM task_dependency WHERE complaint_id = %s "
                        "ORDER BY dependent_task_id, prerequisite_task_id", (complaint_id,)).fetchall()
    history = conn.execute(
        "SELECT a.*, u.name AS who FROM audit_log a LEFT JOIN app_user u USING (user_id) "
        "WHERE a.complaint_id = %s OR a.task_id IN (SELECT task_id FROM task WHERE complaint_id = %s) "
        "ORDER BY a.created_at, a.audit_id", (complaint_id, complaint_id)).fetchall()
    raw = get_mongo().complaints_raw.find_one({"complaint_id": complaint_id}) or {}
    return render_template("reviewer/complaint_detail.html", c=c, tasks=tasks, deps=deps,
                           history=history, photos=raw.get("attachments", []),
                           reject_form=ReasonForm(prefix="reject"))


@bp.route("/complaints/<int:complaint_id>/reject", methods=["POST"])
@role_required("REVIEWER")
def reject(complaint_id):
    """DFD 4.3 -> D2: intake_status = REJECTED, with a reason."""
    form = ReasonForm(prefix="reject")
    if not form.validate_on_submit():
        flash("Rejecting needs a reason of 5 to 500 characters.", "error")
        return redirect(url_for("reviewer.complaint_detail", complaint_id=complaint_id))
    conn = get_db()
    with conn.transaction():
        row = conn.execute("SELECT intake_status FROM complaint WHERE complaint_id = %s FOR UPDATE",
                           (complaint_id,)).fetchone()
        if row is None:
            abort(404)
        if row["intake_status"] not in ("PENDING", "UNDER_REVIEW"):
            flash(f"A complaint that is {row['intake_status']} cannot be rejected.", "error")
            return redirect(url_for("reviewer.complaint_detail", complaint_id=complaint_id))
        conn.execute("UPDATE complaint SET intake_status = 'REJECTED' WHERE complaint_id = %s",
                     (complaint_id,))
        audit(conn, "REJECT", user_id=current_user.id, complaint_id=complaint_id,
              old_status=row["intake_status"], new_status="REJECTED", reason=form.reason.data)
    flash(f"Complaint #{complaint_id} rejected.", "success")
    return redirect(url_for("reviewer.complaint_detail", complaint_id=complaint_id))


@bp.route("/exceptions")
@role_required("REVIEWER")
def exceptions():
    """DFD 5.4 'exception alert': every FAILED task and every task on hold
    (BLOCKED_EXCEPTION), with the prerequisites that caused it."""
    conn = get_db()
    failed = conn.execute(
        "SELECT t.*, a.agency_name FROM task t JOIN agency a USING (agency_id) "
        "WHERE status = 'FAILED' ORDER BY task_id").fetchall()
    held = conn.execute(
        "SELECT t.*, a.agency_name FROM task t JOIN agency a USING (agency_id) "
        "WHERE status = 'BLOCKED_EXCEPTION' ORDER BY task_id").fetchall()
    prereqs = {}
    for h in held:
        prereqs[h["task_id"]] = conn.execute(
            "SELECT p.task_id, p.status, p.task_code FROM task_dependency d "
            "JOIN task p ON p.task_id = d.prerequisite_task_id WHERE d.dependent_task_id = %s "
            "ORDER BY p.task_id", (h["task_id"],)).fetchall()
    return render_template("reviewer/exceptions.html", failed=failed, held=held, prereqs=prereqs,
                           form=ReasonForm())


def _resolve(sql, params, ok_message):
    """Run one resolution function (db/schema.sql) with the reviewer's reason."""
    form = ReasonForm()
    if not form.validate_on_submit():
        flash("Every resolution needs a reason of 5 to 500 characters.", "error")
        return redirect(url_for("reviewer.exceptions"))
    conn = get_db()
    try:
        with conn.transaction():
            conn.execute(sql, (*params, current_user.id, form.reason.data))
    except (psycopg.errors.CheckViolation, psycopg.errors.RaiseException) as e:
        flash(f"Not done: {e.diag.message_primary}.", "error")
        return redirect(url_for("reviewer.exceptions"))
    flash(ok_message, "success")
    return redirect(url_for("reviewer.exceptions"))


@bp.route("/tasks/<int:task_id>/retry", methods=["POST"])
@role_required("REVIEWER")
def retry(task_id):
    return _resolve("SELECT fn_resolve_retry(%s, %s, %s)", (task_id,),
                    f"Task {task_id} is ready again with a new deadline.")


@bp.route("/tasks/<int:task_id>/cancel", methods=["POST"])
@role_required("REVIEWER")
def cancel(task_id):
    return _resolve("SELECT fn_resolve_cancel(%s, %s, %s)", (task_id,), f"Task {task_id} cancelled.")


@bp.route("/dependencies/<int:prereq>/<int:dependent>/waive", methods=["POST"])
@role_required("REVIEWER")
def waive(prereq, dependent):
    return _resolve("SELECT fn_resolve_waive(%s, %s, %s, %s)", (prereq, dependent),
                    f"Task {dependent} no longer waits for task {prereq}.")


@bp.route("/reports")
@role_required("REVIEWER")
def reports():
    """DFD 6.0 'report request / status report'."""
    conn = get_db()
    by_status = conn.execute("SELECT status, count(*) AS n FROM task GROUP BY status ORDER BY status").fetchall()
    by_agency = conn.execute(
        "SELECT a.agency_name, count(t.task_id) AS total, "
        "count(*) FILTER (WHERE t.status = 'COMPLETED') AS done "
        "FROM agency a LEFT JOIN task t USING (agency_id) GROUP BY a.agency_name ORDER BY a.agency_name").fetchall()
    overdue = conn.execute("SELECT o.*, a.agency_name FROM v_task_overdue o JOIN agency a USING (agency_id) "
                           "ORDER BY o.due_at").fetchall()
    intake = conn.execute("SELECT intake_status, count(*) AS n FROM complaint GROUP BY intake_status "
                          "ORDER BY intake_status").fetchall()
    return render_template("reviewer/reports.html", by_status=by_status, by_agency=by_agency,
                           overdue=overdue, intake=intake)
