import logging, os, socket, time, uuid
import click
from flask import Flask, g, request, jsonify, render_template
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from .config import Config
from .extensions import db, login_manager, csrf
from .services.secrets import database_uri
from .models import User
from .monitoring.metrics import http_requests, http_errors, request_duration, db_errors

def create_app(test_config=None):
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)sZ %(levelname)s %(message)s")
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config: app.config.update(test_config)
    if not app.config.get("TESTING") and app.config["FLASK_ENV"] == "production" and len(os.getenv("SECRET_KEY", "")) < 32:
        raise RuntimeError("Production requires a stable SECRET_KEY of at least 32 characters from the approved runtime environment")
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        try: app.config["SQLALCHEMY_DATABASE_URI"] = database_uri()
        except RuntimeError:
            app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite://"
            app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"hide_parameters": True}
            app.extensions["database_config_error"] = True
    db.init_app(app); login_manager.init_app(app); csrf.init_app(app)
    login_manager.login_view = "auth.login"
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.extensions["lab_db_failed"] = False
    @login_manager.user_loader
    def load_user(user_id): return db.session.get(User, int(user_id))
    @app.before_request
    def before_request():
        supplied_id = request.headers.get("X-Request-ID", "")[:128]
        try:
            g.request_id = str(uuid.UUID(supplied_id))
        except ValueError:
            g.request_id = str(uuid.uuid4())
        g.started_at = time.perf_counter()
    @app.after_request
    def after_request(response):
        # CSRF can reject a request before our before_request hook runs.
        g.request_id = getattr(g, "request_id", None) or str(uuid.uuid4())
        response.headers["X-Request-ID"] = g.request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        endpoint = request.endpoint or "unknown"
        duration = time.perf_counter() - getattr(g, "started_at", time.perf_counter())
        http_requests.labels(request.method, endpoint, str(response.status_code)).inc()
        request_duration.labels(request.method, endpoint).observe(duration)
        if response.status_code >= 400: http_errors.labels(str(response.status_code)).inc()
        from flask_login import current_user
        user = current_user.get_id() if current_user.is_authenticated else "anonymous"
        app.logger.info("request_id=%s host=%s method=%s endpoint=%s status=%s duration_ms=%.1f user=%s", g.request_id, socket.gethostname(), request.method, endpoint, response.status_code, duration*1000, user)
        return response
    from .routes import bp
    app.register_blueprint(bp)
    @app.cli.command("init-db")
    def init_db_command():
        """Create application tables if they do not exist."""
        db.create_all()
        click.echo("Database tables are ready.")
    @app.cli.command("seed-demo")
    def seed_demo_command():
        """Load synthetic local learning data; never reset an existing password."""
        if app.config["FLASK_ENV"] != "development":
            raise click.ClickException("Demo data is permitted only in development")
        from .models import Record
        db.create_all()
        user = User.query.filter_by(username="local-demo").first()
        if user is None:
            user = User(username="local-demo", email="demo@example.invalid")
            user.set_password("development-only-demo-password")
            db.session.add(user)
            db.session.flush()
            db.session.add(Record(title="Local readiness exercise", description="Stop the local database and compare /health with /ready.", created_by=user.id))
            db.session.commit()
        click.echo("Synthetic demo data ready; see README for development-only login.")
    @app.get("/health")
    def health(): return jsonify(status="healthy", service="cloudops-portal")
    @app.get("/ready")
    def ready():
        if app.extensions.get("lab_db_failed") or app.extensions.get("database_config_error"):
            db_errors.inc(); return jsonify(status="not_ready", database="unavailable"), 503
        try:
            with db.engine.connect() as connection: connection.execute(text("SELECT 1"))
            return jsonify(status="ready", database="connected")
        except Exception:
            db_errors.inc(); app.logger.exception("Database readiness check failed"); db.session.remove()
            return jsonify(status="not_ready", database="unavailable"), 503
    @app.errorhandler(404)
    def not_found(error): return render_template("errors/404.html"), 404
    @app.errorhandler(500)
    def server_error(error):
        app.logger.exception("Unhandled application error"); db.session.remove()
        return render_template("errors/500.html"), 500
    @app.errorhandler(413)
    def too_large(error): return "File exceeds the 10 MB limit", 413
    return app
