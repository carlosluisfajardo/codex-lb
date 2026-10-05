from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command

from app.db.migrate import _build_alembic_config, check_migration_policy, check_schema_drift, run_upgrade

PARENT = "20260912_000000_merge_thread_cache_and_bridge_retirement_heads"
ACCOUNT = "20261005_000000_add_account_lifecycle_preferences"
PRIORITY = "20261005_000000_add_dashboard_keyless_priority_service_tier"
MERGE = "20261005_010000_merge_account_lifecycle_and_keyless_priority"


def _preserved_data(path: Path) -> tuple[list[tuple], list[tuple], list[tuple], tuple]:
    with sqlite3.connect(path) as db:
        return (
            db.execute("SELECT * FROM accounts ORDER BY id").fetchall(),
            db.execute("SELECT * FROM usage_history ORDER BY id").fetchall(),
            db.execute("SELECT * FROM request_logs ORDER BY id").fetchall(),
            db.execute(
                "SELECT compact_request_budget_seconds, auto_redeem_reset_credits_before_expiry FROM dashboard_settings"
            ).fetchone(),
        )


@pytest.mark.parametrize("starting_revision", [PARENT, ACCOUNT, PRIORITY])
def test_combined_upgrade_backup_rollback_reupgrade_preserves_existing_data(
    tmp_path: Path, starting_revision: str
) -> None:
    path = tmp_path / "persistent.sqlite"
    url = f"sqlite+aiosqlite:///{path}"
    run_upgrade(url, starting_revision, bootstrap_legacy=False)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO accounts (id,email,plan_type,access_token_encrypted,refresh_token_encrypted,"
            "id_token_encrypted,last_refresh,status) VALUES (?,?,?,?,?,?,?,?)",
            (
                "fixture-account",
                "fixture@example.invalid",
                "plus",
                "fixture-a",
                "fixture-r",
                "fixture-i",
                "2026-10-05",
                "ACTIVE",
            ),
        )
        db.execute("INSERT INTO usage_history (account_id,used_percent) VALUES ('fixture-account',23.5)")
        db.execute(
            "INSERT INTO request_logs (account_id,request_id,model,status) "
            "VALUES ('fixture-account','fixture-request','fixture-model','success')"
        )
        db.execute(
            "UPDATE dashboard_settings SET compact_request_budget_seconds=600,auto_redeem_reset_credits_before_expiry=0"
        )
    before = _preserved_data(path)
    result = run_upgrade(url, "head", bootstrap_legacy=False)
    assert result.current_revision == MERGE
    assert check_migration_policy(url) == ()
    assert check_schema_drift(url) == ()
    assert _preserved_data(path) == before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT keyless_priority_service_tier FROM dashboard_settings").fetchone() == (0,)
        db.execute(
            "INSERT INTO account_lifecycle_preferences (account_id,account_incarnation,ends_on_date,revision) "
            "VALUES ('fixture-account','fixture-incarnation','2026-10-31',7)"
        )

    backup = tmp_path / "qualified-backup.sqlite"
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    assert _preserved_data(backup) == before
    assert check_schema_drift(f"sqlite+aiosqlite:///{backup}") == ()
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT ends_on_date,revision FROM account_lifecycle_preferences").fetchone() == (
            "2026-10-31",
            7,
        )

    command.downgrade(_build_alembic_config(url), PARENT)
    assert _preserved_data(path) == before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == (PARENT,)
        assert "keyless_priority_service_tier" not in {
            row[1] for row in db.execute("PRAGMA table_info(dashboard_settings)")
        }
        assert db.execute("SELECT name FROM sqlite_master WHERE name='account_lifecycle_preferences'").fetchall() == []
    run_upgrade(url, "head", bootstrap_legacy=False)
    assert _preserved_data(path) == before
    assert check_schema_drift(url) == ()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT keyless_priority_service_tier FROM dashboard_settings").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM account_lifecycle_preferences").fetchone() == (0,)
