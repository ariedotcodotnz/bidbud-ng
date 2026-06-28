"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-06-27 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("settings"):
        op.create_table(
            "settings",
            sa.Column("key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("value", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.PrimaryKeyConstraint("key"),
        )
    if not inspector.has_table("jobs"):
        op.create_table(
            "jobs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("listing_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("url", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("strategy", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("max_bid", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column(
                "status", sqlmodel.sql.sqltypes.AutoString(), nullable=False,
                server_default="scheduled",
            ),
            sa.Column("end_date", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("current_price", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("min_next_bid", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("bid_count", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("is_leader", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("reserve_met", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("last_action", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("options", sa.Text(), nullable=True),
            sa.Column("created_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("updated_at", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    if not inspector.has_table("bid_log"):
        op.create_table(
            "bid_log",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("job_id", sa.Integer(), nullable=True),
            sa.Column("ts", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("level", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("bid_log"):
        op.drop_table("bid_log")
    if inspector.has_table("jobs"):
        op.drop_table("jobs")
    if inspector.has_table("settings"):
        op.drop_table("settings")
