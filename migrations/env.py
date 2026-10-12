"""
Alembic environment (based on the Flask-Migrate template).

Schema changes are applied automatically when the app starts (see
procuretrack/database.py), after a backup is taken.

NOTE for future migrations on SQLite: never change a column's type with
alter_column(type_=...). In batch mode Alembic copies data with CAST(),
and SQLite's CAST turns text like '30-08-2026' into the number 30.
Copy rows in Python instead, as 0002_v04_dates_and_state does.
"""

import logging
from logging.config import fileConfig

from alembic import context
from flask import current_app

config = context.config

# disable_existing_loggers=False keeps the app's and waitress's loggers working
# when migrations run inside the running app.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
logger = logging.getLogger("alembic.env")


def get_engine():
    return current_app.extensions["migrate"].db.engine


config.set_main_option(
    "sqlalchemy.url",
    get_engine().url.render_as_string(hide_password=False).replace("%", "%%"),
)
target_db = current_app.extensions["migrate"].db


def run_migrations_offline():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_db.metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    # don't create empty migration files when nothing changed
    def process_revision_directives(context, revision, directives):
        if getattr(config.cmd_opts, "autogenerate", False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                logger.info("No changes in schema detected.")

    conf_args = current_app.extensions["migrate"].configure_args
    conf_args.setdefault("process_revision_directives", process_revision_directives)

    with get_engine().connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_db.metadata,
            **conf_args,  # Flask-Migrate adds render_as_batch=True (needed for SQLite)
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
