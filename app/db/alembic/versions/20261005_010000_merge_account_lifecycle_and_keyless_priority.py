"""Join the frozen account lifecycle and keyless priority revisions.

Both independently reviewed additive revisions descend from the same release
head. Preserve their identities for databases that applied either candidate.
"""

revision = "20261005_010000_merge_account_lifecycle_and_keyless_priority"
down_revision = (
    "20261005_000000_add_account_lifecycle_preferences",
    "20261005_000000_add_dashboard_keyless_priority_service_tier",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
