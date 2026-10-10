import logging, os, socket
from datetime import datetime, timezone
from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from .extensions import db
from .models import User, Record
from .monitoring import metrics
from .services.s3 import upload_file

bp = Blueprint("auth", __name__)
log = logging.getLogger(__name__)
@bp.get("/")
def index(): return redirect(url_for("auth.dashboard" if current_user.is_authenticated else "auth.login"))
@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username=request.form.get("username", "").strip(); email=request.form.get("email", "").strip().lower(); password=request.form.get("password", "")
        if not username or len(username)>80 or not email or len(email)>254 or len(password)<10: flash("Enter a username, valid email, and password with at least 10 characters.", "danger")
        else:
            user=User(username=username, email=email); user.set_password(password)
            try:
                db.session.add(user); db.session.commit(); flash("Account created. Sign in to continue.", "success"); return redirect(url_for("auth.login"))
            except IntegrityError: db.session.rollback(); flash("That username or email is already registered.", "danger")
            except SQLAlchemyError: db.session.rollback(); metrics.db_errors.inc(); log.exception("Registration database operation failed"); flash("Registration is temporarily unavailable.", "danger")
    return render_template("register.html")
@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        metrics.login_attempts.inc(); user=User.query.filter_by(username=request.form.get("username", "").strip()).first()
        if user and user.check_password(request.form.get("password", "")):
            login_user(user); metrics.login_success.inc(); log.info("Successful login user_id=%s", user.id); return redirect(url_for("auth.dashboard"))
        metrics.login_failure.inc(); log.warning("Failed login username_supplied=%s", bool(request.form.get("username"))); flash("Invalid username or password.", "danger")
    return render_template("login.html")
@bp.get("/logout")
@login_required
def logout():
    logout_user(); return redirect(url_for("auth.login"))
@bp.get("/dashboard")
@login_required
def dashboard():
    try: total_users=db.session.query(User.id).count(); total_records=db.session.query(Record.id).count(); database="Connected"
    except SQLAlchemyError:
        db.session.rollback(); db.session.remove(); metrics.db_errors.inc(); total_users=total_records="Unavailable"; database="Unavailable"
    return render_template("dashboard.html", total_users=total_users, total_records=total_records, database=database, hostname=socket.gethostname(), version=current_app.config["APP_VERSION"], environment=current_app.config["FLASK_ENV"], now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), s3="Configured" if os.getenv("S3_BUCKET_NAME") else "Not configured")
@bp.get("/records")
@login_required
def records():
    try: items=Record.query.order_by(Record.created_at.desc()).all()
    except SQLAlchemyError: db.session.rollback(); db.session.remove(); metrics.db_errors.inc(); items=[]; flash("Records are temporarily unavailable.", "danger")
    return render_template("records.html", records=items)
@bp.route("/records/add", methods=["GET", "POST"])
@login_required
def add_record():
    if request.method == "POST":
        title=request.form.get("title", "").strip(); description=request.form.get("description", "").strip()
        if not title or len(title)>160 or len(description)>10000: flash("Enter a title (up to 160 characters) and a description up to 10,000 characters.", "danger")
        else:
            try: db.session.add(Record(title=title, description=description, created_by=current_user.id)); db.session.commit(); flash("Record created.", "success"); return redirect(url_for("auth.records"))
            except SQLAlchemyError: db.session.rollback(); db.session.remove(); metrics.db_errors.inc(); flash("Could not save record. Check database availability.", "danger")
    return render_template("add_record.html")
@bp.post("/records/<int:record_id>/delete")
@login_required
def delete_record(record_id):
    record=db.session.get(Record, record_id)
    if not record: abort(404)
    if record.created_by != current_user.id and not current_user.is_admin: abort(403)
    db.session.delete(record); db.session.commit(); flash("Record deleted.", "success"); return redirect(url_for("auth.records"))
@bp.get("/users")
@login_required
def users(): return render_template("users.html", users=User.query.order_by(User.created_at.desc()).all())
@bp.route("/upload", methods=["GET", "POST"])
@login_required
def file_upload():
    key=None
    if request.method == "POST":
        file=request.files.get("file")
        if not file or not file.filename: flash("Choose a file to upload.", "danger")
        else:
            try: key=upload_file(file, current_user.id); metrics.s3_upload.inc(); current_app.logger.info("S3 upload succeeded user_id=%s", current_user.id); flash("Upload completed.", "success")
            except (ValueError, RuntimeError) as e: metrics.s3_upload_failures.inc(); flash(str(e), "danger")
            except Exception: metrics.s3_upload_failures.inc(); current_app.logger.exception("S3 upload failed user_id=%s", current_user.id); flash("S3 upload failed. Check application logs and IAM permissions.", "danger")
    return render_template("upload.html", object_key=key)
@bp.get("/status")
@login_required
def system_status():
    try: db.session.execute(text("SELECT 1")); database="connected"
    except SQLAlchemyError: db.session.rollback(); db.session.remove(); metrics.db_errors.inc(); database="unavailable"
    return render_template("status.html", database=database, hostname=socket.gethostname(), version=current_app.config["APP_VERSION"], environment=current_app.config["FLASK_ENV"], s3="configured" if os.getenv("S3_BUCKET_NAME") else "not configured", now=datetime.now(timezone.utc).isoformat())
@bp.get("/api/status")
@login_required
def api_status():
    try: db.session.execute(text("SELECT 1")); database="connected"
    except SQLAlchemyError: db.session.rollback(); db.session.remove(); metrics.db_errors.inc(); database="unavailable"
    return jsonify(hostname=socket.gethostname(), version=current_app.config["APP_VERSION"], environment=current_app.config["FLASK_ENV"], database=database, s3="configured" if os.getenv("S3_BUCKET_NAME") else "not_configured", timestamp=datetime.now(timezone.utc).isoformat())
@bp.get("/metrics")
def prometheus_metrics(): return metrics.response()
@bp.get("/troubleshooting")
@login_required
def troubleshooting(): return render_template("troubleshooting.html", enabled=os.getenv("ENABLE_LAB_FAILURE_ENDPOINTS", "false").lower()=="true")
def require_lab_admin():
    if os.getenv("ENABLE_LAB_FAILURE_ENDPOINTS", "false").lower() != "true": abort(404)
    if not current_user.is_admin: abort(403)
@bp.post("/lab/fail/db")
@login_required
def fail_db():
    require_lab_admin(); current_app.extensions["lab_db_failed"] = True; return jsonify(status="simulated", database="unavailable")
@bp.post("/lab/recover/db")
@login_required
def recover_db():
    require_lab_admin(); current_app.extensions["lab_db_failed"] = False; return jsonify(status="recovered")
@bp.get("/lab/fail/error")
@login_required
def fail_error(): require_lab_admin(); abort(500)
