"""A SQLite restore fences out writers from other processes from its snapshot read until it ends."""

from __future__ import annotations

import subprocess
import sys
import uuid

import pytest
from sqlalchemy import select

from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountLifecyclePreference, AccountStatus
from app.db.session import SessionLocal
from app.modules.account_lifecycle.repository import (
    AccountLifecycleRepository,
    LifecycleColumns,
    LifecycleKey,
    LifecycleTarget,
    ReplaceOutcome,
    account_incarnation,
    snapshot_token,
)

pytestmark = pytest.mark.integration

_SQLITE_PREFIX = "sqlite+aiosqlite:///"

# A writer in another OS process, with a short busy timeout so a fenced database refuses it quickly.
_OTHER_PROCESS_WRITER = """
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1], timeout=1.0)
try:
    connection.execute(sys.argv[2], tuple(sys.argv[3:]))
    connection.commit()
except sqlite3.OperationalError as exc:
    print(f"refused: {exc}")
    raise SystemExit(3)
print("committed")
"""


def _database_path() -> str:
    url = get_settings().database_url
    if not url.startswith(_SQLITE_PREFIX):
        pytest.skip("the write fence under test is the SQLite branch")
    return url[len(_SQLITE_PREFIX) :]


def _account(account_id: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        codex_installation_id=str(uuid.uuid4()),
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
    )


def _columns(ends_on: str) -> LifecycleColumns:
    return LifecycleColumns(ends_on, None, None, None, None, None, None)


async def _rows() -> dict[str, tuple[str | None, int]]:
    async with SessionLocal() as session:
        result = await session.execute(select(AccountLifecyclePreference))
        return {row.account_id: (row.ends_on_date, row.revision) for row in result.scalars()}


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["first-insert", "unchanged-record-update"])
async def test_another_process_cannot_write_between_the_snapshot_read_and_the_restore_commit(
    db_setup, monkeypatch, race
):
    database = _database_path()
    a, b = _account("acc_fence_a"), _account("acc_fence_b")
    a_key = LifecycleKey(a.id, account_incarnation(a.codex_installation_id))
    b_key = LifecycleKey(b.id, account_incarnation(b.codex_installation_id))
    async with SessionLocal() as session:
        session.add_all([a, b])
        session.add(
            AccountLifecyclePreference(
                account_id=a_key.account_id,
                account_incarnation=a_key.account_incarnation,
                ends_on_date="2026-10-12",
                revision=2,
            )
        )
        if race == "unchanged-record-update":
            session.add(
                AccountLifecyclePreference(
                    account_id=b_key.account_id,
                    account_incarnation=b_key.account_incarnation,
                    ends_on_date="2026-10-20",
                    revision=1,
                )
            )
        await session.commit()
    targets = {a_key: LifecycleTarget(_columns("2026-10-11"), 1)}
    stored = [(a_key, 2)]
    if race == "first-insert":
        statement = (
            "INSERT INTO account_lifecycle_preferences (account_id, account_incarnation, ends_on_date, revision) "
            "VALUES (?, ?, '2026-10-30', 1)"
        )
        arguments = [b_key.account_id, b_key.account_incarnation]
        expected_final = {"acc_fence_a": ("2026-10-11", 3)}
    else:
        targets[b_key] = LifecycleTarget(_columns("2026-10-20"), 1)
        stored.append((b_key, 1))
        statement = (
            "UPDATE account_lifecycle_preferences SET ends_on_date = '2026-10-30', revision = 2 "
            "WHERE account_id = ? AND account_incarnation = ?"
        )
        arguments = [b_key.account_id, b_key.account_incarnation]
        expected_final = {"acc_fence_a": ("2026-10-11", 3), "acc_fence_b": ("2026-10-20", 1)}
    other: list[subprocess.CompletedProcess[str]] = []
    original_list_all = AccountLifecycleRepository.list_all

    async def _read_then_let_another_process_write(self):
        rows = await original_list_all(self)
        other.append(
            subprocess.run(
                [sys.executable, "-c", _OTHER_PROCESS_WRITER, database, statement, *arguments],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        )
        return rows

    monkeypatch.setattr(AccountLifecycleRepository, "list_all", _read_then_let_another_process_write)

    async with SessionLocal() as session:
        outcome, written = await AccountLifecycleRepository(session).replace_all(
            targets, expected_snapshot=snapshot_token(stored)
        )

    (writer,) = other
    assert writer.returncode == 3, (
        f"another process committed inside the restore window: {writer.stdout}{writer.stderr}"
    )
    assert "locked" in writer.stdout
    assert (outcome, written) == (ReplaceOutcome.APPLIED, 1)
    assert await _rows() == expected_final


@pytest.mark.asyncio
async def test_the_fence_is_released_when_the_snapshot_does_not_match(db_setup):
    database = _database_path()
    a = _account("acc_fence_release")
    async with SessionLocal() as session:
        session.add(a)
        await session.commit()
    key = LifecycleKey(a.id, account_incarnation(a.codex_installation_id))

    async with SessionLocal() as session:
        outcome, written = await AccountLifecycleRepository(session).replace_all(
            {key: LifecycleTarget(_columns("2026-10-11"), 1)}, expected_snapshot="stale"
        )
    writer = subprocess.run(
        [
            sys.executable,
            "-c",
            _OTHER_PROCESS_WRITER,
            database,
            "INSERT INTO account_lifecycle_preferences (account_id, account_incarnation, revision) VALUES (?, ?, 1)",
            key.account_id,
            key.account_incarnation,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert (outcome, written) == (ReplaceOutcome.SNAPSHOT_MISMATCH, 0)
    assert writer.returncode == 0, writer.stdout + writer.stderr
