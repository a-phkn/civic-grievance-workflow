"""AGENCY_STAFF: DFD 5.1 Show Ready Tasks and DFD 5.2 Update Task Status.
Scope: own agency only. The agency comes from APP_USER (D1 'staff agency'),
never from the form or URL."""
from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user

from .auth import role_required
from .db import audit, get_db
from .forms import TaskStatusForm

bp = Blueprint("staff", __name__, url_prefix="/staff")

# The part of the state diagram that belongs to agency staff (DFD 5.2).
# Everything else (unlock, retry, cancel) is done by the trigger or a reviewer.
STAFF_NEXT = {
    "READY_FOR_DISPATCH": [("IN_PROGRESS", "Start work")],
    "IN_PROGRESS": [("COMPLETED", "Mark completed"), ("FAILED", "Report a failure")],
}


@bp.route("/tasks")
@role_required("AGENCY_STAFF")
def task_list():
    """DFD 5.1: tasks of MY agency that I can act on."""
    rows = get_db().execute(
        "SELECT t.task_id, t.complaint_id, t.task_code, tt.task_description, t.status, "
        "       t.ready_at, t.due_at, (t.due_at < LOCALTIMESTAMP) AS overdue, "
        "       left(c.description, 80) AS summary "
        "FROM task t JOIN agency_task_type tt USING (agency_id, task_code) "
        "JOIN complaint c USING (complaint_id) "
        "WHERE t.agency_id = %s AND t.status IN ('READY_FOR_DISPATCH', 'IN_PROGRESS') "
        "ORDER BY t.due_at", (current_user.agency_id,)).fetchall()
    forms = {}
    for r in rows:
        f = TaskStatusForm(prefix=f"t{r['task_id']}")
        f.new_status.choices = STAFF_NEXT[r["status"]]
        forms[r["task_id"]] = f
    return render_template("staff/tasks.html", rows=rows, forms=forms)


@bp.route("/tasks/<int:task_id>/status", methods=["POST"])
@role_required("AGENCY_STAFF")
def update_status(task_id):
    """DFD 5.2. 1) the task must be my agency's (else 403, audited);
    2) the new status must be a legal staff step from the current one;
    3) the UPDATE only applies if the status has not changed since step 1.
    The database trigger checks the transition again, then the unlock
    trigger (DFD 5.3) runs inside the same transaction."""
    conn = get_db()
    task = conn.execute("SELECT agency_id, status FROM task WHERE task_id = %s", (task_id,)).fetchone()
    if task is None:
        abort(404)
    if task["agency_id"] != current_user.agency_id:
        audit(conn, "ACCESS_DENIED", user_id=current_user.id,
              reason=f"Staff of agency {current_user.agency_id} tried to update task {task_id}")
        abort(403)

    form = TaskStatusForm(prefix=f"t{task_id}")
    form.new_status.choices = STAFF_NEXT.get(task["status"], [])
    if not form.validate_on_submit():
        flash(f"Task {task_id}: that status change is not allowed from {task['status']}.", "error")
        return redirect(url_for("staff.task_list"))

    with conn.transaction():
        changed = conn.execute(
            "UPDATE task SET status = %s WHERE task_id = %s AND status = %s",
            (form.new_status.data, task_id, task["status"])).rowcount
        if changed:
            audit(conn, "STATUS_CHANGE", user_id=current_user.id, task_id=task_id,
                  old_status=task["status"], new_status=form.new_status.data)
    if not changed:
        flash(f"Task {task_id} changed while you were looking at it. Please check it again.", "error")
    else:
        flash(f"Task {task_id}: {task['status']} \u2192 {form.new_status.data}.", "success")
    return redirect(url_for("staff.task_list"))
