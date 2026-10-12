"""V0.6.2: several items per ticket (ERIC's requisition form has item rows)

- New table ticket_items: description, quantity, unit and the estimated
  price / GST per item. Each existing ticket gets one item from its
  item / quantity / unit / estimated price columns.
- po_lines point at a ticket item (item_id) instead of a ticket (ticket_id).
- procurement_requests: `item` becomes an optional `title` (emptied, since the
  name now lives on the item); quantity, unit, est_unit_price and est_gst_rate
  move to ticket_items and are dropped.

Rows are copied with plain INSERT ... SELECT; no column changes type
(see the note in env.py).

Revision ID: 0006_v062_requisition_items
Revises: 0005_v061_requisition
Create Date: 2026-09-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_v062_requisition_items"
down_revision = "0005_v061_requisition"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ticket_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "ticket_id", sa.Integer, sa.ForeignKey("procurement_requests.id"), nullable=False
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("description", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("est_unit_price", sa.Integer),  # x 100
        sa.Column("est_gst_rate", sa.Integer),  # x 100
    )
    op.create_index("ix_ticket_items_ticket_id", "ticket_items", ["ticket_id"])

    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT INTO ticket_items "
            "(ticket_id, position, description, quantity, unit, est_unit_price, est_gst_rate) "
            "SELECT id, 1, item, COALESCE(quantity, 1), COALESCE(unit, 'Nos'), "
            "est_unit_price, est_gst_rate FROM procurement_requests"
        )
    )

    # PO lines: ticket -> that ticket's (only) item
    with op.batch_alter_table("po_lines") as batch:
        batch.add_column(sa.Column("item_id", sa.Integer))
    conn.execute(
        sa.text(
            "UPDATE po_lines SET item_id = "
            "(SELECT ticket_items.id FROM ticket_items "
            "WHERE ticket_items.ticket_id = po_lines.ticket_id)"
        )
    )
    with op.batch_alter_table("po_lines") as batch:
        batch.drop_index("ix_po_lines_ticket_id")
        batch.drop_column("ticket_id")
        batch.alter_column("item_id", existing_type=sa.Integer, nullable=False)
        batch.create_foreign_key("fk_po_lines_item", "ticket_items", ["item_id"], ["id"])
        batch.create_index("ix_po_lines_item_id", ["item_id"])

    with op.batch_alter_table(
        "procurement_requests", table_kwargs={"sqlite_autoincrement": True}
    ) as batch:
        batch.alter_column(
            "item", new_column_name="title", existing_type=sa.String(200), nullable=True
        )
        batch.drop_column("quantity")
        batch.drop_column("unit")
        batch.drop_column("est_unit_price")
        batch.drop_column("est_gst_rate")
    conn.execute(sa.text("UPDATE procurement_requests SET title = NULL"))


def downgrade():
    raise RuntimeError(
        "Downgrading is not supported. Restore the pre-migration backup from the "
        "data folder's backups/ directory instead."
    )
