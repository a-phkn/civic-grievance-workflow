"""ADMIN: DFD 1.0 Manage Users and Reference Data -> D1 APP_USER, D3 AGENCY,
D4 AGENCY_TASK_TYPE, and a 'reference-data change entry' in D7 AUDIT_LOG.
Admin audit rows have neither complaint nor task; the change is described
in `reason` (design rule)."""
import psycopg
from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user

from .auth import ph, role_required
from .db import audit, get_db
from .forms import AgencyForm, SlaForm, StaffAccountForm, TaskTypeForm

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _forms(conn):
    agencies = conn.execute("SELECT agency_id, agency_name FROM agency ORDER BY agency_name").fetchall()
    tt = TaskTypeForm(prefix="tt")
    tt.agency_id.choices = [(a["agency_id"], a["agency_name"]) for a in agencies]
    staff = StaffAccountForm(prefix="user")
    staff.agency_id.choices = [(0, "None (reviewer)")] + tt.agency_id.choices
    return AgencyForm(prefix="ag"), tt, staff


def _page(conn, ag, tt, staff, code=200):
    data = dict(
        agencies=conn.execute("SELECT * FROM agency ORDER BY agency_name").fetchall(),
        types=conn.execute("SELECT tt.*, a.agency_name FROM agency_task_type tt JOIN agency a USING (agency_id) "
                           "ORDER BY a.agency_name, tt.task_code").fetchall(),
        users=conn.execute("SELECT u.user_id, u.name, u.email, u.role, a.agency_name FROM app_user u "
                           "LEFT JOIN agency a USING (agency_id) WHERE u.role <> 'CITIZEN' "
                           "ORDER BY u.role, u.name").fetchall(),
        changes=conn.execute("SELECT a.created_at, a.reason, u.name AS who FROM audit_log a "
                             "LEFT JOIN app_user u USING (user_id) WHERE a.action = 'ADMIN_CHANGE' "
                             "ORDER BY a.audit_id DESC LIMIT 10").fetchall(),
    )
    return render_template("admin/dashboard.html", ag=ag, tt=tt, staff=staff,
                           sla_form=SlaForm(prefix="sla"), **data), code


@bp.route("/")
@role_required("ADMIN")
def dashboard():
    conn = get_db()
    return _page(conn, *_forms(conn))


@bp.route("/agencies", methods=["POST"])
@role_required("ADMIN")
def add_agency():
    conn = get_db()
    ag, tt, staff = _forms(conn)
    if ag.validate_on_submit():
        try:
            with conn.transaction():
                conn.execute("INSERT INTO agency (agency_name, description) VALUES (%s, %s)",
                             (ag.agency_name.data, ag.description.data or None))
                audit(conn, "ADMIN_CHANGE", user_id=current_user.id,
                      reason=f"Added agency {ag.agency_name.data}")
            flash(f"Agency {ag.agency_name.data} added.", "success")
            return redirect(url_for("admin.dashboard"))
        except psycopg.errors.UniqueViolation:
            ag.agency_name.errors.append("An agency with this name already exists.")
    return _page(conn, ag, tt, staff, 400)


@bp.route("/task-types", methods=["POST"])
@role_required("ADMIN")
def add_task_type():
    conn = get_db()
    ag, tt, staff = _forms(conn)
    if tt.validate_on_submit():
        try:
            with conn.transaction():
                conn.execute("INSERT INTO agency_task_type (agency_id, task_code, task_description, sla_hours) "
                             "VALUES (%s, %s, %s, %s)",
                             (tt.agency_id.data, tt.task_code.data, tt.task_description.data or None,
                              tt.sla_hours.data))
                audit(conn, "ADMIN_CHANGE", user_id=current_user.id,
                      reason=f"Added task type {tt.task_code.data} (agency {tt.agency_id.data}), "
                             f"SLA {tt.sla_hours.data} h")
            flash(f"Task type {tt.task_code.data} added.", "success")
            return redirect(url_for("admin.dashboard"))
        except psycopg.errors.UniqueViolation:
            tt.task_code.errors.append("This agency already has a task type with this code.")
    return _page(conn, ag, tt, staff, 400)


@bp.route("/task-types/<int:agency_id>/<code>/sla", methods=["POST"])
@role_required("ADMIN")
def change_sla(agency_id, code):
    conn = get_db()
    form = SlaForm(prefix="sla")
    if not form.validate_on_submit():
        flash("SLA must be a whole number of hours from 1 to 8760.", "error")
        return redirect(url_for("admin.dashboard"))
    with conn.transaction():
        row = conn.execute("SELECT sla_hours FROM agency_task_type WHERE agency_id = %s AND task_code = %s "
                           "FOR UPDATE", (agency_id, code)).fetchone()
        if row is None:
            abort(404)
        conn.execute("UPDATE agency_task_type SET sla_hours = %s WHERE agency_id = %s AND task_code = %s",
                     (form.sla_hours.data, agency_id, code))
        # e.g. "PIPE_REPAIR SLA 48 -> 36 h" (format from the design doc)
        audit(conn, "ADMIN_CHANGE", user_id=current_user.id,
              reason=f"{code} SLA {row['sla_hours']} → {form.sla_hours.data} h")
    flash(f"{code} SLA changed to {form.sla_hours.data} h. New deadlines use it; "
          f"existing deadlines are not changed.", "success")
    return redirect(url_for("admin.dashboard"))


@bp.route("/users", methods=["POST"])
@role_required("ADMIN")
def add_user():
    conn = get_db()
    ag, tt, staff = _forms(conn)
    if staff.validate_on_submit():
        agency = staff.agency_id.data or None
        try:
            with conn.transaction():
                conn.execute("INSERT INTO app_user (name, email, password_hash, role, agency_id) "
                             "VALUES (%s, %s, %s, %s, %s)",
                             (staff.name.data, staff.email.data, ph.hash(staff.password.data),
                              staff.role.data, agency))
                audit(conn, "ADMIN_CHANGE", user_id=current_user.id,
                      reason=f"Created {staff.role.data} account {staff.email.data}")
            flash(f"Account for {staff.email.data} created.", "success")
            return redirect(url_for("admin.dashboard"))
        except psycopg.errors.UniqueViolation:
            staff.email.errors.append("An account with this email already exists.")
    return _page(conn, ag, tt, staff, 400)
