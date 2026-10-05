"""Add dashboard_settings.keyless_priority_service_tier (keyless Fast opt-in).

Revision ID: 20261005_000000_add_dashboard_keyless_priority_service_tier
Revises: 20260912_000000_merge_thread_cache_and_bridge_retirement_heads
Create Date: 2026-10-05

Boolean, NOT NULL, server default false: existing installs keep today's
behaviour (keyless requests forward the client's service tier unchanged) until
an operator turns the opt-in on through ``PUT /api/settings``. No data backfill.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20261005_000000_add_dashboard_keyless_priority_service_tier"
down_revision = "20260912_000000_merge_thread_cache_and_bridge_retirement_heads"
branch_labels = None
depends_on = None

_TABLE = "dashboard_settings"
_COLUMN = "keyless_priority_service_tier"


def _columns(connection: Connection) -> set[str]:
    if not sa.inspect(connection).has_table(_TABLE):
        return set()
    return {column["name"] for column in sa.inspect(connection).get_columns(_TABLE)}


def upgrade() -> None:
    columns = _columns(op.get_bind())
    if columns and _COLUMN not in columns:
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.add_column(sa.Column(_COLUMN, sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    columns = _columns(op.get_bind())
    if columns and _COLUMN in columns:
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN)
