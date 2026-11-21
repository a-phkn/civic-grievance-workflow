"""Application factory: wires the blueprints (one per role / DFD process),
CSRF protection, login, security headers and error pages."""
from flask import Flask, render_template
from flask_login import current_user
from flask_wtf.csrf import CSRFError, CSRFProtect

from .config import Config
from .db import close_db

csrf = CSRFProtect()


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    app.config["UPLOAD_FOLDER"].mkdir(parents=True, exist_ok=True)

    csrf.init_app(app)                 # every POST must carry a valid CSRF token
    from .auth import bp as auth_bp, login_manager
    login_manager.init_app(app)
    from .citizen import bp as citizen_bp
    from .staff import bp as staff_bp
    from .reviewer import bp as reviewer_bp
    from .admin import bp as admin_bp
    for bp in (auth_bp, citizen_bp, staff_bp, reviewer_bp, admin_bp):
        app.register_blueprint(bp)
    app.teardown_appcontext(close_db)

    @app.after_request
    def security_headers(resp):
        # Flask security docs: CSP, nosniff, framing. No inline scripts or
        # styles are used anywhere, so the policy can be strict.
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        if current_user.is_authenticated:
            resp.headers["Cache-Control"] = "no-store"   # no private pages in the browser cache
        return resp

    def error(code, title, message):
        return render_template("error.html", code=code, title=title, message=message), code

    @app.errorhandler(CSRFError)
    def csrf_error(e):
        return error(400, "Form expired or invalid",
                     "The form's security token was missing or out of date. Go back, reload the page and try again.")

    app.register_error_handler(403, lambda e: error(403, "Not allowed", "Your role does not have access to this page."))
    app.register_error_handler(404, lambda e: error(404, "Not found", "That page or record does not exist."))
    app.register_error_handler(413, lambda e: error(413, "File too large", "Uploads must be 5 MB or smaller."))
    app.register_error_handler(500, lambda e: error(500, "Something went wrong",
                                                    "The error has been logged. Please try again."))

    @app.context_processor
    def nav():
        return {"ROLE_LABEL": {"CITIZEN": "Citizen", "AGENCY_STAFF": "Agency staff",
                               "REVIEWER": "Reviewer", "ADMIN": "Admin"}}

    with app.app_context():
        from .db import init_mongo_indexes
        try:
            init_mongo_indexes()
        except Exception as exc:   # app still starts; complaint submission will report the outage
            app.logger.warning("MongoDB not reachable at startup: %s", exc)
    return app
