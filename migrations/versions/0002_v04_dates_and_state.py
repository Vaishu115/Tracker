"""V0.4: real date columns, case state (Active / On Hold / Cancelled), updated_at

V0.3 stored dates as text in mixed formats ('30-08-2026 16:39:01' for
timestamps, '2026-08-31' for dates). This migration rebuilds both tables
with proper DATE / DATETIME columns and converts every row in Python.

Values that can't be read are cleared and a MIGRATION_NOTE entry recording
the original text is added to that ticket's history, so nothing is lost silently.

Also handles V0.3.0 databases that are missing the category, procurement
type, quantity and priority columns added in V0.3.1.

Revision ID: 0002_v04_dates_and_state
Revises: 0001_v03_baseline
Create Date: 2026-09-24
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "0002_v04_dates_and_state"
down_revision = "0001_v03_baseline"
branch_labels = None
depends_on = None

DATETIME_FORMATS = (
    "%d-%m-%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%d-%m-%Y",
    "%Y-%m-%d",
)
DATE_FORMATS = (
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
)


def parse(value, formats, as_date):
    """Returns (parsed value or None, True if the value was present but unreadable)."""
    if value is None:
        return None, False
    text = str(value).strip()
    if not text:
        return None, False
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return (parsed.date() if as_date else parsed), False
    return None, True


def create_new_tables():
    requests = op.create_table(
        "procurement_requests_v04",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticket_id", sa.String(20), nullable=False, unique=True),
        sa.Column("item", sa.String(200), nullable=False),
        sa.Column("project", sa.String(100)),
        sa.Column("owner", sa.String(100)),
        sa.Column("category", sa.String(50)),
        sa.Column("procurement_type", sa.String(50)),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("request_date", sa.DateTime),
        sa.Column("required_by", sa.Date),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("selected_vendor", sa.String(200)),
        sa.Column("po_number", sa.String(50)),
        sa.Column("payment_status", sa.String(30), nullable=False),
        sa.Column("fulfilment_status", sa.String(30), nullable=False),
        sa.Column("expected_delivery", sa.Date),
        sa.Column("archived", sa.Boolean, nullable=False),
        sa.Column("updated_at", sa.DateTime),
        sqlite_autoincrement=True,
    )
    history = op.create_table(
        "status_history_v04",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticket_id", sa.String(20), nullable=False),
        sa.Column("timestamp", sa.DateTime),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("old_status", sa.String(50)),
        sa.Column("new_status", sa.String(50)),
        sa.Column("comment", sa.Text),
        sqlite_autoincrement=True,
    )
    return requests, history


def upgrade():
    conn = op.get_bind()
    old_columns = {c["name"] for c in sa.inspect(conn).get_columns("procurement_requests")}
    old_requests = (
        conn.execute(sa.text("SELECT * FROM procurement_requests ORDER BY id")).mappings().all()
    )
    old_history = conn.execute(sa.text("SELECT * FROM status_history ORDER BY id")).mappings().all()

    new_requests, new_history = create_new_tables()
    migrated_at = datetime.now()
    notes = []

    request_rows = []
    for old in old_requests:

        def get(name, default=None, old=old):
            value = old[name] if name in old_columns else None
            return default if value is None or value == "" else value

        ticket_id = get("ticket_id") or f"LEGACY-{old['id']}"
        status = get("status", "Requirement Raised")
        row = {
            "id": old["id"],
            "ticket_id": ticket_id,
            "item": get("item", "(unnamed item)"),
            "project": get("project"),
            "owner": get("owner"),
            "category": get("category"),
            "procurement_type": get("procurement_type"),
            "priority": get("priority", "Normal"),
            "description": get("description"),
            "status": status,
            "state": "Active",
            "selected_vendor": get("selected_vendor"),
            "po_number": get("po_number"),
            "payment_status": get("payment_status", "Pending"),
            "fulfilment_status": get("fulfilment_status", "Not Ordered"),
            "archived": bool(int(get("archived", 0))),
            "updated_at": migrated_at,
        }

        try:
            row["quantity"] = int(get("quantity", 1))
        except (TypeError, ValueError):
            notes.append(
                (
                    ticket_id,
                    status,
                    f"Quantity '{get('quantity')}' could not be read and was set to 1.",
                )
            )
            row["quantity"] = 1

        for field, formats, as_date in (
            ("request_date", DATETIME_FORMATS, False),
            ("required_by", DATE_FORMATS, True),
            ("expected_delivery", DATE_FORMATS, True),
        ):
            raw = get(field)
            row[field], unreadable = parse(raw, formats, as_date)
            if unreadable:
                label = field.replace("_", " ")
                notes.append(
                    (
                        ticket_id,
                        status,
                        f"The {label} value '{raw}' could not be read as a date and was cleared.",
                    )
                )

        request_rows.append(row)

    history_rows = []
    for old in old_history:
        timestamp, unreadable = parse(old["timestamp"], DATETIME_FORMATS, False)
        comment = old["comment"]
        if unreadable:
            comment = f"{comment or ''} [original time: {old['timestamp']}]".strip()
        history_rows.append(
            {
                "id": old["id"],
                "ticket_id": old["ticket_id"] or "",
                "timestamp": timestamp,
                "event_type": old["event_type"] or "STATUS_CHANGE",
                "old_status": old["old_status"],
                "new_status": old["new_status"],
                "comment": comment,
            }
        )

    # new rows, without id (the database assigns one after the copied rows)
    note_rows = [
        {
            "ticket_id": ticket_id,
            "timestamp": migrated_at,
            "event_type": "MIGRATION_NOTE",
            "old_status": status,
            "new_status": status,
            "comment": note,
        }
        for ticket_id, status, note in notes
    ]

    for table, rows in (
        (new_requests, request_rows),
        (new_history, history_rows),
        (new_history, note_rows),
    ):
        if rows:
            op.bulk_insert(table, rows)

    op.drop_table("status_history")
    op.drop_table("procurement_requests")
    op.rename_table("procurement_requests_v04", "procurement_requests")
    op.rename_table("status_history_v04", "status_history")
    op.create_index("ix_status_history_ticket_id", "status_history", ["ticket_id"])


def downgrade():
    raise RuntimeError(
        "Downgrading below V0.4 is not supported. Restore the pre-migration "
        "backup from the data folder's backups/ directory instead."
    )
