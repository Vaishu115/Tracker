"""
ProcureTrack - Procurement Ticketing System for ERIC Robotics.

create_app() builds the Flask application. See config.py for the settings
that can be changed per machine, and README.md for how to run it.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, g, render_template
from flask_wtf.csrf import CSRFError

from .config import (
    LEGACY_DB_PATH,
    MIGRATIONS_DIR,
    PROJECT_ROOT,
    auth_settings,
    default_data_dir,
    load_secret_key,
    offsite_backup_dir,
)
from .extensions import csrf, db, migrate

__version__ = "0.6.2"


def create_app(overrides=None):
    app = Flask(
        __name__,
        template_folder=str(PROJECT_ROOT / "templates"),
        static_folder=str(PROJECT_ROOT / "static"),
    )

    app.config.update(
        DATA_DIR=default_data_dir(),
        OFFSITE_BACKUP_DIR=offsite_backup_dir(),
        LEGACY_DB_PATH=LEGACY_DB_PATH,
        BACKUP_KEEP=30,
        AUTO_MIGRATE=True,
        SQLALCHEMY_ENGINE_OPTIONS={},
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        **auth_settings(),
    )
    if overrides:
        app.config.update(overrides)

    data_dir = Path(app.config["DATA_DIR"])
    data_dir.mkdir(parents=True, exist_ok=True)
    app.config["DATA_DIR"] = data_dir
    app.config.setdefault("BACKUP_DIR", data_dir / "backups")

    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        app.config["SQLALCHEMY_DATABASE_URI"] = (
            os.environ.get("DATABASE_URL")
            or f"sqlite:///{(data_dir / 'procurement.db').as_posix()}"
        )
    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        # wait for a busy database instead of failing when two people save at once
        app.config["SQLALCHEMY_ENGINE_OPTIONS"].setdefault("connect_args", {"timeout": 15})

    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = load_secret_key(data_dir)

    configure_logging(app)

    if app.config.get("PRODUCTION"):
        # behind one reverse proxy (IIS / nginx / Azure) that terminates HTTPS
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
        if app.config["AUTH_MODE"] != "microsoft":
            app.logger.error(
                "PROCURETRACK_ENV=production but Microsoft 365 sign-in is not configured "
                "(PROCURETRACK_MS_TENANT_ID / _CLIENT_ID / _CLIENT_SECRET): nobody can sign in."
            )

    db.init_app(app)
    migrate.init_app(app, db, directory=str(MIGRATIONS_DIR))
    csrf.init_app(app)

    from .views import auth, main, pos, projects, settings, tickets, users, vendors

    app.register_blueprint(auth.bp)
    app.register_blueprint(main.bp)
    app.register_blueprint(tickets.bp)
    app.register_blueprint(pos.bp)
    app.register_blueprint(vendors.bp)
    app.register_blueprint(projects.bp)
    app.register_blueprint(settings.bp)
    app.register_blueprint(users.bp)

    register_template_helpers(app)
    register_error_handlers(app)

    from .commands import register_commands

    register_commands(app)

    if app.config["AUTO_MIGRATE"]:
        from .database import prepare_database

        prepare_database(app)

    return app


def configure_logging(app):
    if app.testing:
        return
    log_dir = app.config["DATA_DIR"] / "logs"
    log_dir.mkdir(exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "procuretrack.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
    app.logger.propagate = False  # Flask already prints to the console; avoid duplicate lines


def register_template_helpers(app):
    from . import constants

    @app.template_filter("fmt_date")
    def fmt_date(value):
        return value.strftime("%d %b %Y") if value else "-"

    @app.template_filter("stage_label")
    def stage_label(value):
        return constants.STAGE_DISPLAY_NAMES.get(value, value)

    @app.template_filter("fmt_dmy")
    def fmt_dmy(value):
        """25-08-2026: the date style used on ERIC's printed forms."""
        return value.strftime("%d-%m-%Y") if value else ""

    @app.template_filter("rate")
    def rate(value):
        """GST rate without trailing zeros: 18.00 -> 18, 0.25 -> 0.25."""
        if value is None:
            return ""
        return f"{value.normalize():f}" if hasattr(value, "normalize") else str(value)

    @app.template_filter("fmt_datetime")
    def fmt_datetime(value):
        return value.strftime("%d %b %Y, %H:%M") if value else "-"

    from .money import amount_in_words, format_amount

    @app.template_filter("money")
    def money(value, currency=constants.BASE_CURRENCY):
        return format_amount(value, currency)

    @app.template_filter("amount_words")
    def amount_words(value, currency=constants.BASE_CURRENCY):
        return amount_in_words(value, currency)

    @app.template_filter("state_name")
    def state_name(code):
        return constants.INDIAN_STATES.get(code or "", "")

    from .permissions import can

    @app.context_processor
    def globals_for_templates():
        return {
            "app_version": __version__,
            "current_user": g.get("user"),
            "can": can,
            "auth_mode": app.config["AUTH_MODE"],
            "STATE_ACTIVE": constants.STATE_ACTIVE,
            "STATE_ON_HOLD": constants.STATE_ON_HOLD,
            "STATE_CANCELLED": constants.STATE_CANCELLED,
        }


def register_error_handlers(app):
    def error_page(title, message, code):
        return render_template("error.html", title=title, message=message, code=code), code

    @app.errorhandler(CSRFError)
    def csrf_error(e):
        return error_page(
            "Form expired",
            "This form was open too long or was submitted from another page. Go back, reload and try again.",
            400,
        )

    @app.errorhandler(400)
    def bad_request(e):
        return error_page("Bad request", e.description, 400)

    @app.errorhandler(403)
    def forbidden(e):
        return error_page(
            "Not allowed",
            "Your role doesn't allow this action. Ask the purchase team or the ProcureTrack admin if you need it done.",
            403,
        )

    @app.errorhandler(404)
    def not_found(e):
        message = (
            e.description
            if "does not exist" in (e.description or "")
            else "This page does not exist."
        )
        return error_page("Not found", message, 404)

    @app.errorhandler(500)
    def server_error(e):
        db.session.rollback()
        return error_page(
            "Something went wrong",
            "The error has been logged. Please try again, and tell the ProcureTrack admin if it keeps happening.",
            500,
        )
