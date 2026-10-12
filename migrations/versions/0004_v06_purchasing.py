"""V0.6: projects, vendors, company settings, purchase orders; ticket unit

- New tables: projects, vendors, company_settings, purchase_orders, po_lines.
- procurement_requests: `project` (free text) becomes `project_id` (link to
  projects). Every distinct project name is turned into a project; names that
  differ only in capitals or spaces are merged. Adds `unit` (default 'Nos').

Money columns are integers (ExactDecimal in models.py): 1250 = 12.50.
No existing column changes type (see the note in env.py).

Revision ID: 0004_v06_purchasing
Revises: 0003_v05_users_and_roles
Create Date: 2026-09-24
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "0004_v06_purchasing"
down_revision = "0003_v05_users_and_roles"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("description", sa.Text),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_table(
        "vendors",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("contact_person", sa.String(100)),
        sa.Column("email", sa.String(254)),
        sa.Column("phone", sa.String(50)),
        sa.Column("address", sa.Text),
        sa.Column("city", sa.String(100)),
        sa.Column("state_code", sa.String(2)),
        sa.Column("country", sa.String(100), nullable=False),
        sa.Column("gstin", sa.String(15)),
        sa.Column("pan", sa.String(10)),
        sa.Column("payment_terms", sa.String(200)),
        sa.Column("default_currency", sa.String(3), nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_table(
        "company_settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("address", sa.Text),
        sa.Column("state_code", sa.String(2)),
        sa.Column("gstin", sa.String(15)),
        sa.Column("pan", sa.String(10)),
        sa.Column("email", sa.String(254)),
        sa.Column("phone", sa.String(50)),
        sa.Column("delivery_address", sa.Text),
        sa.Column("po_prefix", sa.String(30), nullable=False),
        sa.Column("default_terms", sa.Text),
    )
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("po_number", sa.String(50), unique=True),
        sa.Column("vendor_id", sa.Integer, sa.ForeignKey("vendors.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("exchange_rate", sa.Integer, nullable=False),  # x 10^4
        sa.Column("tax_type", sa.String(10), nullable=False),
        sa.Column("po_date", sa.Date),
        sa.Column("delivery_date", sa.Date),
        sa.Column("delivery_address", sa.Text),
        sa.Column("payment_terms", sa.String(200)),
        sa.Column("terms", sa.Text),
        sa.Column("notes", sa.Text),
        sa.Column("vendor_snapshot", sa.JSON),
        sa.Column("company_snapshot", sa.JSON),
        sa.Column("created_by_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime),
        sa.Column("issued_by_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("issued_at", sa.DateTime),
        sa.Column("cancelled_by_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("cancelled_at", sa.DateTime),
        sa.Column("cancel_reason", sa.Text),
    )
    op.create_table(
        "po_lines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("po_id", sa.Integer, sa.ForeignKey("purchase_orders.id"), nullable=False),
        sa.Column(
            "ticket_id", sa.Integer, sa.ForeignKey("procurement_requests.id"), nullable=False
        ),
        sa.Column("description", sa.String(200), nullable=False),
        sa.Column("hsn_sac", sa.String(10)),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("unit_price", sa.Integer, nullable=False),  # x 100
        sa.Column("gst_rate", sa.Integer, nullable=False),  # x 100
    )
    op.create_index("ix_po_lines_po_id", "po_lines", ["po_id"])
    op.create_index("ix_po_lines_ticket_id", "po_lines", ["ticket_id"])

    with op.batch_alter_table(
        "procurement_requests", table_kwargs={"sqlite_autoincrement": True}
    ) as batch:
        batch.add_column(sa.Column("project_id", sa.Integer))
        batch.add_column(sa.Column("unit", sa.String(20), nullable=False, server_default="Nos"))
        batch.create_foreign_key(
            "fk_procurement_requests_project", "projects", ["project_id"], ["id"]
        )

    # free-text project names -> project rows
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, project FROM procurement_requests WHERE project IS NOT NULL")
    ).all()
    project_ids = {}  # normalised name -> id
    for ticket_id, name in rows:
        name = " ".join((name or "").split())[:100]
        if not name:
            continue
        key = name.lower()
        if key not in project_ids:
            conn.execute(
                sa.text(
                    "INSERT INTO projects (name, active, created_at) VALUES (:name, :active, :now)"
                ),
                # same text format SQLAlchemy uses for DateTime on SQLite
                {"name": name, "active": True, "now": f"{datetime.now():%Y-%m-%d %H:%M:%S.%f}"},
            )
            project_ids[key] = conn.execute(
                sa.text("SELECT id FROM projects WHERE name = :name"), {"name": name}
            ).scalar_one()
        conn.execute(
            sa.text("UPDATE procurement_requests SET project_id = :pid WHERE id = :tid"),
            {"pid": project_ids[key], "tid": ticket_id},
        )

    with op.batch_alter_table(
        "procurement_requests", table_kwargs={"sqlite_autoincrement": True}
    ) as batch:
        batch.drop_column("project")


def downgrade():
    raise RuntimeError(
        "Downgrading below V0.6 is not supported. Restore the pre-migration "
        "backup from the data folder's backups/ directory instead."
    )
