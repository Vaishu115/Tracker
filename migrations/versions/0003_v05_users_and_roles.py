"""V0.5: users and roles; who raised each ticket and who made each change

Only adds a table and nullable columns; no existing column changes type
(see the note in env.py). Existing tickets and history keep empty user
fields: they were created before sign-in existed.

Revision ID: 0003_v05_users_and_roles
Revises: 0002_v04_dates_and_state
Create Date: 2026-09-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_v05_users_and_roles"
down_revision = "0002_v04_dates_and_state"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_admin", sa.Boolean, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("ms_oid", sa.String(64), unique=True),
        sa.Column("created_at", sa.DateTime),
        sa.Column("last_login_at", sa.DateTime),
    )

    with op.batch_alter_table(
        "procurement_requests", table_kwargs={"sqlite_autoincrement": True}
    ) as batch:
        batch.add_column(sa.Column("created_by_id", sa.Integer))
        batch.create_foreign_key(
            "fk_procurement_requests_created_by", "users", ["created_by_id"], ["id"]
        )

    with op.batch_alter_table(
        "status_history", table_kwargs={"sqlite_autoincrement": True}
    ) as batch:
        batch.add_column(sa.Column("user_id", sa.Integer))
        batch.add_column(sa.Column("actor_name", sa.String(100)))
        batch.create_foreign_key("fk_status_history_user", "users", ["user_id"], ["id"])


def downgrade():
    with op.batch_alter_table(
        "status_history", table_kwargs={"sqlite_autoincrement": True}
    ) as batch:
        batch.drop_constraint("fk_status_history_user", type_="foreignkey")
        batch.drop_column("actor_name")
        batch.drop_column("user_id")

    with op.batch_alter_table(
        "procurement_requests", table_kwargs={"sqlite_autoincrement": True}
    ) as batch:
        batch.drop_constraint("fk_procurement_requests_created_by", type_="foreignkey")
        batch.drop_column("created_by_id")

    op.drop_table("users")
