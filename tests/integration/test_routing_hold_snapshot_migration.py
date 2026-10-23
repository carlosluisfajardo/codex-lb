from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command

from app.db.migrate import _build_alembic_config, check_migration_policy, check_schema_drift, run_upgrade

PARENT = "20261005_010000_merge_account_lifecycle_and_keyless_priority"
REVISION = "20261022_000000_add_routing_hold_and_sticky_generation"


def test_routing_hold_snapshot_upgrade_and_rollback_preserve_existing_rows(tmp_path: Path) -> None:
    path = tmp_path / "hold-snapshot.sqlite"
    url = f"sqlite+aiosqlite:///{path}"
    run_upgrade(url, PARENT, bootstrap_legacy=False)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO accounts (id,codex_installation_id,email,plan_type,access_token_encrypted,"
            "refresh_token_encrypted,id_token_encrypted,last_refresh,status,routing_policy) "
            "VALUES ('fixture','fixture-installation','fixture@example.invalid','plus',"
            "X'01',X'02',X'03','2026-10-22','reauth_required','preserve')"
        )
        for key in ("first", "second"):
            db.execute("INSERT INTO sticky_sessions (key,kind,account_id) VALUES (?,'codex_session','fixture')", (key,))
    result = run_upgrade(url, "head", bootstrap_legacy=False)
    assert result.current_revision == REVISION
    assert check_migration_policy(url) == () and check_schema_drift(url) == ()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status,routing_policy,routing_held FROM accounts").fetchone() == (
            "reauth_required",
            "preserve",
            0,
        )
        rows = db.execute("SELECT key,account_id,ownership_generation FROM sticky_sessions ORDER BY key").fetchall()
        assert [(row[0], row[1]) for row in rows] == [("first", "fixture"), ("second", "fixture")]
        assert all(row[2] for row in rows) and len({row[2] for row in rows}) == 2
    command.downgrade(_build_alembic_config(url), PARENT)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status,routing_policy FROM accounts").fetchone() == ("reauth_required", "preserve")
        assert db.execute("SELECT key,account_id FROM sticky_sessions ORDER BY key").fetchall() == [
            ("first", "fixture"),
            ("second", "fixture"),
        ]
    run_upgrade(url, "head", bootstrap_legacy=False)
    assert check_schema_drift(url) == ()
