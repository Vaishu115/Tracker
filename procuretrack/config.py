"""
Configuration.

Everything that differs between machines comes from environment variables,
so the same code runs on a laptop and on the office server. They can also
be put in a `.env` file in the project folder (see .env.example); real
environment variables take precedence over the file.

    PROCURETRACK_DATA_DIR    Folder holding the database, backups and logs.
                             Default: <project>/instance
    PROCURETRACK_OFFSITE_BACKUP_DIR
                             Optional. Every backup is also copied here
                             (e.g. a OneDrive / SharePoint / network folder).
    PROCURETRACK_SECRET_KEY  Signs sessions and CSRF tokens. If unset, a random
                             key is generated once and stored in the data folder.
    DATABASE_URL             Optional. Use a different database (e.g. PostgreSQL).
                             Default: SQLite file inside the data folder.
    PROCURETRACK_ENV         "production" on the live server: the test sign-in is
                             switched off completely, session cookies are HTTPS-only,
                             and X-Forwarded-* headers from ONE reverse proxy (IIS,
                             nginx, Azure) are trusted so links and the Microsoft
                             redirect use https. Default: development.
    PROCURETRACK_HOST        Address waitress listens on (default 127.0.0.1).
    PROCURETRACK_PORT        Port waitress listens on (default 5000).

  Microsoft 365 sign-in (from the Entra ID app registration). When all three
  are set, staff sign in with Microsoft; otherwise the local test sign-in is used.
    PROCURETRACK_MS_TENANT_ID
    PROCURETRACK_MS_CLIENT_ID
    PROCURETRACK_MS_CLIENT_SECRET
    PROCURETRACK_MS_REDIRECT_URI   Optional. Default: http://<host>/auth/callback
    PROCURETRACK_ADMIN_EMAILS      Comma-separated; these users become admins
                                   when they sign in (to set up the first admin).
"""

import os
import secrets
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"

# Read <project>/.env so every way of starting the app (python app.py, flask CLI,
# scripts\*.bat, scheduled backup) uses the same settings.
load_dotenv(PROJECT_ROOT / ".env", override=False)

# Database file used by V0.3 (next to app.py). Copied into the data folder on first run.
LEGACY_DB_PATH = PROJECT_ROOT / "procurement.db"


def default_data_dir():
    return Path(os.environ.get("PROCURETRACK_DATA_DIR") or PROJECT_ROOT / "instance")


def offsite_backup_dir():
    value = os.environ.get("PROCURETRACK_OFFSITE_BACKUP_DIR", "").strip()
    return Path(value) if value else None


def is_production():
    return os.environ.get("PROCURETRACK_ENV", "").strip().lower() == "production"


def auth_settings():
    env = os.environ.get
    tenant = env("PROCURETRACK_MS_TENANT_ID", "").strip()
    client = env("PROCURETRACK_MS_CLIENT_ID", "").strip()
    secret = env("PROCURETRACK_MS_CLIENT_SECRET", "").strip()
    admins = env("PROCURETRACK_ADMIN_EMAILS", "")
    production = is_production()
    if tenant and client and secret:
        mode = "microsoft"
    else:
        # the test sign-in never runs in production; there nobody can sign in
        # until Microsoft 365 is configured
        mode = "none" if production else "dev"
    return {
        "PRODUCTION": production,
        "AUTH_MODE": mode,
        "SESSION_COOKIE_SECURE": production,
        "MS_TENANT_ID": tenant,
        "MS_CLIENT_ID": client,
        "MS_CLIENT_SECRET": secret,
        "MS_REDIRECT_URI": env("PROCURETRACK_MS_REDIRECT_URI", "").strip(),
        "ADMIN_EMAILS": {e.strip().lower() for e in admins.split(",") if e.strip()},
        # Eligible approvers may approve tickets they raised themselves.
        # Such approvals are marked "Self-approved" in the timeline.
        "ALLOW_SELF_APPROVAL": True,
        "PERMANENT_SESSION_LIFETIME": timedelta(hours=12),
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_HTTPONLY": True,
    }


def load_secret_key(data_dir):
    env_key = os.environ.get("PROCURETRACK_SECRET_KEY")
    if env_key:
        return env_key

    key_file = data_dir / "secret_key"
    if key_file.exists():
        return key_file.read_text().strip()

    key = secrets.token_hex(32)
    key_file.write_text(key)
    return key
