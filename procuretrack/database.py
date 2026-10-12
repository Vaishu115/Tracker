"""
Database housekeeping: moving the V0.3 database into the data folder,
upgrading the schema on startup, and backups.
"""

import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import flask_migrate
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import make_url

from .extensions import db

# Revision that matches the schema V0.3 created with raw SQL.
BASELINE_REVISION = "0001_v03_baseline"


def sqlite_path(app):
    """Path of the SQLite file, or None when using another database."""
    url = make_url(app.config["SQLALCHEMY_DATABASE_URI"])
    if url.get_backend_name() != "sqlite" or url.database in (None, "", ":memory:"):
        return None
    return Path(url.database)


def copy_legacy_database(app):
    """First run after upgrading from V0.3: copy procurement.db into the data folder."""
    legacy = app.config.get("LEGACY_DB_PATH")
    target = sqlite_path(app)
    if not legacy or not target or target.exists() or not Path(legacy).exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(legacy, target)
    app.logger.warning(
        "Copied existing database %s to %s. The app now uses the new location; "
        "the old file is left untouched.",
        legacy,
        target,
    )


def backup_database(app, reason="manual"):
    """
    Consistent copy of the SQLite database using SQLite's backup API
    (safe while the app is running). Keeps the newest BACKUP_KEEP files.
    Returns the backup path, or None if there is nothing to back up.
    """
    source = sqlite_path(app)
    if source is None or not source.exists():
        return None

    backup_dir = Path(app.config["BACKUP_DIR"])
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"procurement-{datetime.now():%Y%m%d-%H%M%S}-{reason}.db"
    counter = 1
    while dest.exists():
        dest = backup_dir / f"procurement-{datetime.now():%Y%m%d-%H%M%S}-{reason}-{counter}.db"
        counter += 1

    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(dest)) as dst:
        src.backup(dst)

    prune_backups(backup_dir, app.config["BACKUP_KEEP"])
    app.logger.info("Database backed up to %s", dest)

    offsite = app.config.get("OFFSITE_BACKUP_DIR")
    if offsite:
        # a second copy on another disk / cloud folder; failing here must not
        # lose the local backup, so only log it
        try:
            Path(offsite).mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, Path(offsite) / dest.name)
            prune_backups(Path(offsite), app.config["BACKUP_KEEP"])
            app.logger.info("Backup also copied to %s", offsite)
        except OSError as exc:
            app.logger.error("Could not copy backup to off-site folder %s: %s", offsite, exc)

    return dest


def prune_backups(folder, keep):
    """Delete all but the newest `keep` backup files in folder."""
    # newest first; the name (which starts with the timestamp) breaks ties
    backups = sorted(
        folder.glob("procurement-*.db"), key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True
    )
    for old in backups[keep:]:
        old.unlink()


def prepare_database(app):
    """Bring the database schema up to date. Backs up first if anything will change."""
    copy_legacy_database(app)

    with app.app_context():
        config = app.extensions["migrate"].migrate.get_config()
        head = ScriptDirectory.from_config(config).get_current_head()

        with db.engine.connect() as conn:
            tables = set(inspect(conn).get_table_names())
            current = MigrationContext.configure(conn).get_current_revision()

        if not tables:
            # Usually right on a first install. If you expected existing data, the
            # app is looking in the wrong place: check PROCURETRACK_DATA_DIR / .env.
            app.logger.warning(
                "No existing database found. Creating a new, empty one at %s",
                app.config["SQLALCHEMY_DATABASE_URI"],
            )

        if current is None and "procurement_requests" in tables:
            # V0.3 database: created without migrations. Mark it as the baseline.
            flask_migrate.stamp(revision=BASELINE_REVISION)
            current = BASELINE_REVISION

        if current != head:
            if current is not None:
                backup_database(app, "pre-migration")
            app.logger.info("Upgrading database schema from %s to %s", current, head)
            flask_migrate.upgrade()
