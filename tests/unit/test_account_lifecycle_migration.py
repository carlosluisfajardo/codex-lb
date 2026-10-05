from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.migrate import _build_alembic_config, check_migration_policy, check_schema_drift, run_upgrade
from app.db.migration_url import to_sync_database_url
from app.db.models import Account, AccountStatus

_PARENT_REVISION = "20260912_000000_merge_thread_cache_and_bridge_retirement_heads"
_REVISION = "20261005_000000_add_account_lifecycle_preferences"
_TABLE = "account_lifecycle_preferences"


def _db_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def _account_rows(connection) -> list[tuple[str, str, str]]:
    return [
        tuple(row)
        for row in connection.execute(text("SELECT id, email, routing_policy FROM accounts ORDER BY id")).all()
    ]


def _head(url: str) -> str:
    (head,) = ScriptDirectory.from_config(_build_alembic_config(url)).get_heads()
    return head


def test_revision_sits_on_the_release_head_inside_a_single_head_graph(tmp_path: Path) -> None:
    script = ScriptDirectory.from_config(_build_alembic_config(_db_url(tmp_path / "graph.db")))

    (head,) = script.get_heads()
    assert _REVISION in {revision.revision for revision in script.iterate_revisions(head, "base")}
    assert script.get_revision(_REVISION).down_revision == _PARENT_REVISION


def test_upgrade_downgrade_upgrade_is_additive_and_keeps_accounts(tmp_path: Path) -> None:
    url = _db_url(tmp_path / "lifecycle.db")
    run_upgrade(url, _PARENT_REVISION, bootstrap_legacy=False)
    engine = create_engine(to_sync_database_url(url))
    try:
        encryptor = TokenEncryptor()
        with Session(engine) as session:
            session.add(
                Account(
                    id="acc_fixture_migrated",
                    email="migrated@example.com",
                    plan_type="plus",
                    routing_policy="burn_first",
                    access_token_encrypted=encryptor.encrypt("a"),
                    refresh_token_encrypted=encryptor.encrypt("r"),
                    id_token_encrypted=encryptor.encrypt("i"),
                    last_refresh=utcnow(),
                    status=AccountStatus.ACTIVE,
                )
            )
            session.commit()
        with engine.connect() as connection:
            accounts_before = _account_rows(connection)
            tables_before = set(inspect(connection).get_table_names())
            assert _TABLE not in tables_before

        config = _build_alembic_config(url)
        command.upgrade(config, _REVISION)
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert set(inspector.get_table_names()) == tables_before | {_TABLE}
            assert {column["name"] for column in inspector.get_columns(_TABLE)} == {
                "account_id",
                "account_incarnation",
                "ends_on_date",
                "ends_on_time",
                "ends_on_timezone",
                "renews_on_date",
                "renews_on_time",
                "renews_on_timezone",
                "cancellation_status",
                "revision",
                "created_at",
                "updated_at",
            }
            assert inspector.get_pk_constraint(_TABLE)["constrained_columns"] == ["account_id", "account_incarnation"]
            assert inspector.get_foreign_keys(_TABLE) == []
            assert _account_rows(connection) == accounts_before
        assert check_migration_policy(url) == ()
        assert check_schema_drift(url) == ()

        with engine.begin() as connection:
            connection.execute(
                text(
                    f"INSERT INTO {_TABLE} (account_id, account_incarnation, ends_on_date, revision) "
                    "VALUES ('acc_fixture_migrated', 'incarnation', '2026-10-12', 1)"
                )
            )

        result = run_upgrade(url, _REVISION, bootstrap_legacy=False)
        assert result.current_revision == _REVISION
        with engine.connect() as connection:
            assert connection.execute(text(f"SELECT ends_on_date, revision FROM {_TABLE}")).all() == [("2026-10-12", 1)]

        command.downgrade(config, _PARENT_REVISION)
        with engine.connect() as connection:
            assert set(inspect(connection).get_table_names()) == tables_before
            assert _account_rows(connection) == accounts_before

        command.upgrade(config, _REVISION)
        with engine.connect() as connection:
            assert connection.execute(text(f"SELECT COUNT(*) FROM {_TABLE}")).scalar_one() == 0
            assert _account_rows(connection) == accounts_before
        assert check_schema_drift(url) == ()
    finally:
        engine.dispose()


def test_upgrade_tolerates_a_table_that_already_exists(tmp_path: Path) -> None:
    url = _db_url(tmp_path / "guarded.db")
    run_upgrade(url, _PARENT_REVISION, bootstrap_legacy=False)
    engine = create_engine(to_sync_database_url(url))
    try:
        with engine.begin() as connection:
            connection.execute(text(f"CREATE TABLE {_TABLE} (account_id VARCHAR NOT NULL PRIMARY KEY)"))

        result = run_upgrade(url, "head", bootstrap_legacy=False)

        assert result.current_revision == _head(url)
    finally:
        engine.dispose()
