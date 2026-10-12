"""V0.6.1: purchase requisition fields

- procurement_requests: recommended vendor, estimated unit price and GST rate
  (filled in by the purchase team; required before sending for approval)
- users: department (printed on the requisition form)
- company_settings: plant (printed on the requisition form)

Only adds nullable columns; no existing column changes type.

Revision ID: 0005_v061_requisition
Revises: 0004_v06_purchasing
Create Date: 2026-09-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_v061_requisition"
down_revision = "0004_v06_purchasing"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table(
        "procurement_requests", table_kwargs={"sqlite_autoincrement": True}
    ) as batch:
        batch.add_column(sa.Column("recommended_vendor_id", sa.Integer))
        batch.add_column(sa.Column("est_unit_price", sa.Integer))  # x 100
        batch.add_column(sa.Column("est_gst_rate", sa.Integer))  # x 100
        batch.create_foreign_key(
            "fk_procurement_requests_recommended_vendor",
            "vendors",
            ["recommended_vendor_id"],
            ["id"],
        )

    op.add_column("users", sa.Column("department", sa.String(100)))
    op.add_column("company_settings", sa.Column("plant", sa.String(200)))


def downgrade():
    raise RuntimeError(
        "Downgrading is not supported. Restore the pre-migration backup from the "
        "data folder's backups/ directory instead."
    )
