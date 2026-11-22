"""Login, logout, citizen registration and role-based access.

Flask-Login only tracks WHO is logged in (the session). WHAT they may do
comes from APP_USER.role, checked on every protected route by
@role_required. The staff member's agency is read from APP_USER (D1), never
from the browser (DFD 5.1, 5.2).
"""
from functools import wraps
import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   request, session, url_for)
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user

from .db import audit, get_db
from .forms import LoginForm, RegisterForm

bp = Blueprint("auth", __name__)
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to continue."
login_manager.session_protection = "strong"   # session dropped if IP/browser fingerprint changes

# Argon2id with the library defaults (RFC 9106 low-memory profile: 64 MiB,
# 3 passes, 4 lanes), above OWASP's minimum of 19 MiB / 2 passes.
ph = PasswordHasher()
# Used when the email does not exist, so a wrong email takes as long as a
# wrong password and response time does not reveal which emails are registered.
_DUMMY_HASH = ph.hash("not-a-real-password-0")

ROLE_HOME = {
    "CITIZEN": "citizen.my_complaints",
    "AGENCY_STAFF": "staff.task_list",
    "REVIEWER": "reviewer.complaints",
    "ADMIN": "admin.dashboard",
}


class User(UserMixin):
    def __init__(self, row):
        self.id = row["user_id"]
        self.name = row["name"]
        self.email = row["email"]
        self.role = row["role"]
        self.agency_id = row["agency_id"]


@login_manager.user_loader
def load_user(user_id):
    row = get_db().execute(
        "SELECT user_id, name, email, role, agency_id FROM app_user WHERE user_id = %s",
        (int(user_id),)).fetchone()
    return User(row) if row else None


def role_required(*roles):
    """Allow only the listed roles. Anyone else gets 403, and the attempt is audited."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                audit(get_db(), "ACCESS_DENIED", user_id=current_user.id,
                      reason=f"{current_user.role} tried {request.method} {request.path}")
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def _recent_failures(conn, user_id):
    """Wrong passwords for this account in the last LOGIN_LOCK_MINUTES (read from AUDIT_LOG)."""
    return conn.execute(
        "SELECT count(*) AS n FROM audit_log WHERE user_id = %s AND action = 'LOGIN_FAILED' "
        "AND created_at > LOCALTIMESTAMP - make_interval(mins => %s)",
        (user_id, current_app.config["LOGIN_LOCK_MINUTES"])).fetchone()["n"]


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for(ROLE_HOME[current_user.role]))
    form = LoginForm()
    if form.validate_on_submit():
        conn = get_db()
        row = conn.execute(
            "SELECT user_id, name, email, role, agency_id, password_hash FROM app_user WHERE email = %s",
            (form.email.data,)).fetchone()
        if row is None:
            try:
                ph.verify(_DUMMY_HASH, form.password.data)
            except VerificationError:
                pass
            flash("Incorrect email or password.", "error")
            return render_template("auth/login.html", form=form), 401

        if _recent_failures(conn, row["user_id"]) >= current_app.config["LOGIN_MAX_FAILURES"]:
            flash(f"Too many failed attempts. Try again in "
                  f"{current_app.config['LOGIN_LOCK_MINUTES']} minutes.", "error")
            return render_template("auth/login.html", form=form), 429

        try:
            ph.verify(row["password_hash"], form.password.data)
        except (VerificationError, InvalidHashError):
            audit(conn, "LOGIN_FAILED", user_id=row["user_id"], reason="Wrong password")
            flash("Incorrect email or password.", "error")
            return render_template("auth/login.html", form=form), 401

        if ph.check_needs_rehash(row["password_hash"]):      # library advice: rehash after login
            conn.execute("UPDATE app_user SET password_hash = %s WHERE user_id = %s",
                         (ph.hash(form.password.data), row["user_id"]))
        session.clear()                                       # fresh session, no carried-over data
        session.permanent = True                              # 30-minute idle timeout (config.py)
        login_user(User(row))
        audit(conn, "LOGIN", user_id=row["user_id"])
        return redirect(url_for(ROLE_HOME[row["role"]]))   # never a URL from the request: no open redirect
    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=["POST"])          # POST + CSRF token: a link on another site cannot log you out
@login_required
def logout():
    logout_user()
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/register", methods=["GET", "POST"])
def register():
    """Citizen registration (DFD 1.0). Staff and reviewer accounts are created by the admin."""
    if current_user.is_authenticated:
        return redirect(url_for(ROLE_HOME[current_user.role]))
    form = RegisterForm()
    if form.validate_on_submit():
        conn = get_db()
        try:
            with conn.transaction():
                uid = conn.execute(
                    "INSERT INTO app_user (name, email, password_hash, role) "
                    "VALUES (%s, %s, %s, 'CITIZEN') RETURNING user_id",
                    (form.name.data, form.email.data, ph.hash(form.password.data))).fetchone()["user_id"]
                audit(conn, "REGISTER", user_id=uid, reason="Citizen self-registration")
        except psycopg.errors.UniqueViolation:
            form.email.errors.append("An account with this email already exists. Log in instead.")
            return render_template("auth/register.html", form=form), 409
        flash("Account created. You can log in now.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/register.html", form=form)


@bp.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for(ROLE_HOME[current_user.role]))
    return redirect(url_for("auth.login"))
