"""add operator-entered account lifecycle preferences

Revision ID: 20261005_000000_add_account_lifecycle_preferences
Revises: 20260912_000000_merge_thread_cache_and_bridge_retirement_heads
Create Date: 2026-10-05

Additive: one new table, no foreign key, no backfill. Downgrade drops only this
table, so export the lifecycle preferences first (``codex-lb account-lifecycle
export``) when the data must survive a rollback.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261005_000000_add_account_lifecycle_preferences"
down_revision = "20260912_000000_merge_thread_cache_and_bridge_retirement_heads"
branch_labels = None
depends_on = None

_TABLE_NAME = "account_lifecycle_preferences"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table(_TABLE_NAME):
        return
    op.create_table(
        _TABLE_NAME,
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("account_incarnation", sa.String(length=64), nullable=False),
        sa.Column("ends_on_date", sa.String(length=10), nullable=True),
        sa.Column("ends_on_time", sa.String(length=8), nullable=True),
        sa.Column("ends_on_timezone", sa.String(length=64), nullable=True),
        sa.Column("renews_on_date", sa.String(length=10), nullable=True),
        sa.Column("renews_on_time", sa.String(length=8), nullable=True),
        sa.Column("renews_on_timezone", sa.String(length=64), nullable=True),
        sa.Column("cancellation_status", sa.String(length=16), nullable=True),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("account_id", "account_incarnation"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE_NAME):
        return
    op.drop_table(_TABLE_NAME)
