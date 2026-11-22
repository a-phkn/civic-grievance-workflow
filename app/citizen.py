"""CITIZEN: DFD 2.0 Register Complaint and DFD 6.0 Report Status (own complaints only)."""
import uuid
from datetime import datetime, timezone

from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   send_from_directory, url_for)
from flask_login import current_user

from .auth import role_required
from .db import audit, get_db, get_mongo
from .forms import ComplaintForm

bp = Blueprint("citizen", __name__, url_prefix="/citizen")

# File signatures ("magic bytes"): the extension can be renamed, the content cannot.
SIGNATURES = {"jpg": b"\xff\xd8\xff", "jpeg": b"\xff\xd8\xff", "png": b"\x89PNG\r\n\x1a\n"}

# Words shown to citizens instead of internal status codes
TASK_STATUS_TEXT = {
    "BLOCKED": "Waiting for another step", "READY_FOR_DISPATCH": "Ready for the agency",
    "IN_PROGRESS": "Being worked on", "COMPLETED": "Done", "FAILED": "Problem reported",
    "CANCELLED": "Cancelled", "BLOCKED_EXCEPTION": "On hold for review",
}


def _save_photo(file_storage):
    """Validate and store a photo in D10. Returns (relative path, error)."""
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in current_app.config["ALLOWED_PHOTO_TYPES"]:
        return None, "Only JPG or PNG images are accepted."
    head = file_storage.stream.read(8)
    file_storage.stream.seek(0)
    if not head.startswith(SIGNATURES[ext]):
        return None, "That file is not a real JPG or PNG image."
    # Random name: the user's file name is never used on disk (no path tricks, no clashes).
    name = f"{uuid.uuid4().hex}.{'jpg' if ext == 'jpeg' else ext}"
    file_storage.save(current_app.config["UPLOAD_FOLDER"] / name)
    return name, None


@bp.route("/complaints/new", methods=["GET", "POST"])
@role_required("CITIZEN")
def new_complaint():
    """DFD 2.0: complaint -> D2 COMPLAINT (PostgreSQL) + D8 complaints_raw
    (MongoDB) + photo -> D10. PostgreSQL is the authority; its transaction
    stays open until MongoDB has the document, so either both keep the
    complaint or neither does (Reconciled Design: 'Complaint submission
    across both databases')."""
    form = ComplaintForm()
    if form.validate_on_submit():
        photo = None
        if form.photo.data and form.photo.data.filename:
            photo, err = _save_photo(form.photo.data)
            if err:
                form.photo.errors.append(err)
                return render_template("citizen/new_complaint.html", form=form), 400

        conn, mongo = get_db(), get_mongo()
        mongo_written = None
        try:
            with conn.transaction():
                cid = conn.execute(
                    "INSERT INTO complaint (citizen_id, description) VALUES (%s, %s) RETURNING complaint_id",
                    (current_user.id, form.description.data)).fetchone()["complaint_id"]
                audit(conn, "COMPLAINT_SUBMITTED", user_id=current_user.id, complaint_id=cid,
                      new_status="PENDING")
                # Only the complaint text and photo path go to MongoDB, never the
                # citizen's name or email (design rule for the NLP step).
                mongo.complaints_raw.insert_one({
                    "complaint_id": cid,
                    "original_text": form.description.data,
                    "attachments": [photo] if photo else [],
                    "submitted_at": datetime.now(timezone.utc),
                })
                mongo_written = cid
            # leaving the block commits PostgreSQL
        except Exception:
            current_app.logger.exception("Complaint submission failed")
            if mongo_written is not None:             # PostgreSQL commit failed after MongoDB succeeded
                mongo.complaints_raw.delete_one({"complaint_id": mongo_written})
            if photo:
                (current_app.config["UPLOAD_FOLDER"] / photo).unlink(missing_ok=True)
            flash("Your complaint could not be saved. Please try again.", "error")
            return render_template("citizen/new_complaint.html", form=form), 503

        flash(f"Complaint #{cid} submitted. You can follow its progress here.", "success")
        return redirect(url_for("citizen.complaint_detail", complaint_id=cid))
    return render_template("citizen/new_complaint.html", form=form)


@bp.route("/complaints")
@role_required("CITIZEN")
def my_complaints():
    """DFD 6.0 for a citizen: only their own complaints (WHERE citizen_id = me)."""
    rows = get_db().execute(
        "SELECT p.*, c.created_at, left(c.description, 90) AS summary "
        "FROM v_complaint_progress p JOIN complaint c USING (complaint_id) "
        "WHERE p.citizen_id = %s ORDER BY c.created_at DESC", (current_user.id,)).fetchall()
    return render_template("citizen/my_complaints.html", rows=rows)


@bp.route("/complaints/<int:complaint_id>")
@role_required("CITIZEN")
def complaint_detail(complaint_id):
    conn = get_db()
    c = conn.execute("SELECT * FROM complaint WHERE complaint_id = %s AND citizen_id = %s",
                     (complaint_id, current_user.id)).fetchone()
    if c is None:
        # Same answer whether it does not exist or belongs to someone else,
        # so a citizen cannot probe for other people's complaint numbers.
        abort(404)
    tasks = conn.execute(
        "SELECT t.task_id, a.agency_name, tt.task_description, t.status, t.due_at, t.completed_at "
        "FROM task t JOIN agency a USING (agency_id) "
        "JOIN agency_task_type tt USING (agency_id, task_code) "
        "WHERE t.complaint_id = %s ORDER BY t.task_id", (complaint_id,)).fetchall()
    # Rationale explanation for a waiting step: which earlier steps it waits
    # for that are not done yet (TASK_DEPENDENCY, the same rows DFD 5.3 uses).
    by_id = {t["task_id"]: t for t in tasks}
    waits_for = {}
    for d in conn.execute("SELECT prerequisite_task_id, dependent_task_id FROM task_dependency "
                          "WHERE complaint_id = %s ORDER BY prerequisite_task_id", (complaint_id,)):
        prereq = by_id[d["prerequisite_task_id"]]
        if prereq["status"] != "COMPLETED":
            waits_for.setdefault(d["dependent_task_id"], []).append(prereq)
    # Rationale explanation: the reviewer's reason is shown to the citizen
    # (it is already stored in AUDIT_LOG by reviewer.reject, DFD 4.3).
    rejection = None
    if c["intake_status"] == "REJECTED":
        rejection = conn.execute(
            "SELECT reason, created_at FROM audit_log WHERE complaint_id = %s AND action = 'REJECT' "
            "ORDER BY audit_id DESC LIMIT 1", (complaint_id,)).fetchone()
    raw = get_mongo().complaints_raw.find_one({"complaint_id": complaint_id}) or {}
    return render_template("citizen/complaint_detail.html", c=c, tasks=tasks, waits_for=waits_for, rejection=rejection,
                           photos=raw.get("attachments", []), status_text=TASK_STATUS_TEXT)


@bp.route("/photo/<int:complaint_id>/<name>")
@role_required("CITIZEN", "REVIEWER")
def photo(complaint_id, name):
    """Photos are served only to the complaint's owner or a reviewer, and only
    if MongoDB lists that exact file for that complaint."""
    if current_user.role == "CITIZEN":
        owns = get_db().execute("SELECT 1 FROM complaint WHERE complaint_id = %s AND citizen_id = %s",
                                (complaint_id, current_user.id)).fetchone()
        if not owns:
            abort(404)
    raw = get_mongo().complaints_raw.find_one({"complaint_id": complaint_id, "attachments": name})
    if not raw:
        abort(404)
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], name)
