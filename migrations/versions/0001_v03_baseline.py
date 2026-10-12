"""V0.3 baseline: the schema V0.3 created with raw SQL

Existing V0.3 databases are stamped with this revision instead of running it.

Revision ID: 0001_v03_baseline
Revises:
Create Date: 2026-09-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_v03_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "procurement_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticket_id", sa.Text, unique=True),
        sa.Column("item", sa.Text, nullable=False),
        sa.Column("project", sa.Text),
        sa.Column("owner", sa.Text),
        sa.Column("category", sa.Text),
        sa.Column("procurement_type", sa.Text),
        sa.Column("quantity", sa.Integer, server_default="1"),
        sa.Column("priority", sa.Text, server_default="Normal"),
        sa.Column("description", sa.Text),
        sa.Column("request_date", sa.Text),
        sa.Column("required_by", sa.Text),
        sa.Column("status", sa.Text),
        sa.Column("selected_vendor", sa.Text),
        sa.Column("po_number", sa.Text),
        sa.Column("payment_status", sa.Text, server_default="Pending"),
        sa.Column("fulfilment_status", sa.Text, server_default="Not Ordered"),
        sa.Column("expected_delivery", sa.Text),
        sa.Column("archived", sa.Integer, server_default="0"),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "status_history",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticket_id", sa.Text),
        sa.Column("timestamp", sa.Text),
        sa.Column("event_type", sa.Text),
        sa.Column("old_status", sa.Text),
        sa.Column("new_status", sa.Text),
        sa.Column("comment", sa.Text),
        sqlite_autoincrement=True,
    )


def downgrade():
    op.drop_table("status_history")
    op.drop_table("procurement_requests")
