import click
from flask import current_app
from sqlalchemy import func, select

from .database import backup_database
from .extensions import db
from .models import ROLE_LABELS, User


def register_commands(app):

    @app.cli.command("backup")
    @click.option("--reason", default="scheduled", help="Tag added to the backup file name.")
    def backup(reason):
        """Back up the database into the data folder's backups/ directory."""
        path = backup_database(current_app, reason)
        if path is None:
            raise click.ClickException("Nothing to back up (no SQLite database found).")
        click.echo(f"Backup written to {path}")

    @app.cli.command("add-user")
    @click.option("--email", required=True, help="Microsoft 365 sign-in address.")
    @click.option("--name", required=True)
    @click.option(
        "--role", type=click.Choice(list(ROLE_LABELS)), default="requester", show_default=True
    )
    @click.option("--admin", is_flag=True, help="Can manage users and delete tickets.")
    def add_user(email, name, role, admin):
        """Add a user (use this to create the first admin)."""
        email = email.strip().lower()
        if db.session.scalar(select(User).where(func.lower(User.email) == email)):
            raise click.ClickException(f"{email} already exists. Change roles on the Users page.")
        db.session.add(User(email=email, name=name.strip(), role=role, is_admin=admin))
        db.session.commit()
        click.echo(f"Added {name} <{email}> as {ROLE_LABELS[role]}{' + Admin' if admin else ''}")
