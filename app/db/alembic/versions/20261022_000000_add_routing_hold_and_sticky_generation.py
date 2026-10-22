"""Add independent routing holds and ownership snapshot identity."""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "20261022_000000_add_routing_hold_and_sticky_generation"
down_revision = "20261005_010000_merge_account_lifecycle_and_keyless_priority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("routing_held", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("sticky_sessions", sa.Column("ownership_generation", sa.String(36), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT key, kind FROM sticky_sessions")).all()
    for key, kind in rows:
        bind.execute(
            sa.text("UPDATE sticky_sessions SET ownership_generation = :generation WHERE key = :key AND kind = :kind"),
            {"generation": str(uuid.uuid4()), "key": key, "kind": kind},
        )


def downgrade() -> None:
    op.drop_column("sticky_sessions", "ownership_generation")
    op.drop_column("accounts", "routing_held")
