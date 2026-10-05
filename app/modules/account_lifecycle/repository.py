from __future__ import annotations

import hashlib
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import utcnow
from app.db.models import Account, AccountLifecyclePreference
from app.db.session import sqlite_writer_section
from app.modules.account_lifecycle.schemas import MAX_STORED_REVISION

_INSERT_FNS = {"postgresql": pg_insert, "sqlite": sqlite_insert}


@dataclass(frozen=True, slots=True)
class LifecycleKey:
    """One record: the exact account id plus the account row it was written for."""

    account_id: str
    account_incarnation: str


@dataclass(frozen=True, slots=True)
class LifecycleColumns:
    ends_on_date: str | None
    ends_on_time: str | None
    ends_on_timezone: str | None
    renews_on_date: str | None
    renews_on_time: str | None
    renews_on_timezone: str | None
    cancellation_status: str | None

    @classmethod
    def of(cls, row: AccountLifecyclePreference) -> LifecycleColumns:
        return cls(
            ends_on_date=row.ends_on_date,
            ends_on_time=row.ends_on_time,
            ends_on_timezone=row.ends_on_timezone,
            renews_on_date=row.renews_on_date,
            renews_on_time=row.renews_on_time,
            renews_on_timezone=row.renews_on_timezone,
            cancellation_status=row.cancellation_status,
        )


CLEARED_COLUMNS = LifecycleColumns(None, None, None, None, None, None, None)


@dataclass(frozen=True, slots=True)
class LifecycleWrite:
    """Replace a record's fields if its stored revision still equals ``expected_revision`` (0 = no record)."""

    key: LifecycleKey
    columns: LifecycleColumns
    expected_revision: int
    new_revision: int


@dataclass(frozen=True, slots=True)
class LifecycleTarget:
    """A record a restore must end with, and the revision its backup recorded."""

    columns: LifecycleColumns
    recorded_revision: int


class LifecycleRevisionOverflowError(Exception):
    """A restore would push a revision past the column range; nothing was written."""


class ReplaceOutcome(Enum):
    APPLIED = "applied"
    SNAPSHOT_MISMATCH = "snapshot_mismatch"
    CONFLICT = "conflict"


def account_incarnation(codex_installation_id: str) -> str:
    """Opaque marker of one account row: a uuid4 minted with the row and kept by in-place credential replacement."""

    return hashlib.sha256(codex_installation_id.encode()).hexdigest()


def snapshot_token(records: Iterable[tuple[LifecycleKey, int]]) -> str:
    """Digest of every stored record key and revision; any accepted write changes it."""

    digest = hashlib.sha256()
    for key, revision in sorted(records, key=lambda record: (record[0].account_id, record[0].account_incarnation)):
        digest.update(f"{key.account_id}\t{key.account_incarnation}\t{revision}\n".encode())
    return digest.hexdigest()


def _key(row: AccountLifecyclePreference) -> LifecycleKey:
    return LifecycleKey(row.account_id, row.account_incarnation)


class AccountLifecycleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def visible_account_incarnations(self, account_ids: Collection[str]) -> dict[str, str]:
        """``account_id -> incarnation`` for the ids among ``account_ids`` that exist and are not pending deletion."""

        if not account_ids:
            return {}
        result = await self._session.execute(
            select(Account.id, Account.codex_installation_id)
            .where(Account.id.in_(account_ids))
            .where(Account.delete_requested_at.is_(None))
        )
        return {account_id: account_incarnation(installation_id) for account_id, installation_id in result.all()}

    async def get(self, key: LifecycleKey) -> AccountLifecyclePreference | None:
        result = await self._session.execute(
            select(AccountLifecyclePreference)
            .where(AccountLifecyclePreference.account_id == key.account_id)
            .where(AccountLifecyclePreference.account_incarnation == key.account_incarnation)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> Sequence[AccountLifecyclePreference]:
        result = await self._session.execute(
            select(AccountLifecyclePreference)
            .order_by(AccountLifecyclePreference.account_id, AccountLifecyclePreference.account_incarnation)
            .execution_options(populate_existing=True)
        )
        return result.scalars().all()

    async def save(self, key: LifecycleKey, columns: LifecycleColumns, *, expected_revision: int) -> bool:
        """Apply one compare-and-set write; ``False`` means the revision was stale and nothing changed."""

        write = LifecycleWrite(key, columns, expected_revision, expected_revision + 1)
        async with sqlite_writer_section():
            if not await self._write(write):
                await self._session.rollback()
                return False
            await self._session.commit()
            return True

    async def replace_all(
        self,
        targets: Mapping[LifecycleKey, LifecycleTarget],
        *,
        expected_snapshot: str,
    ) -> tuple[ReplaceOutcome, int]:
        """Make the stored fields equal ``targets`` in one transaction, guarded by the namespace snapshot.

        Stored records missing from ``targets`` are cleared, never deleted. Every written record ends
        above both its stored revision and the revision its backup recorded, so a draft based on any
        revision the backup knew can never match again. Returns the outcome and the write count.
        """

        async with sqlite_writer_section():
            try:
                if self._session.get_bind().dialect.name == "postgresql":
                    # Keep concurrent dashboard writes (including first writes of new records)
                    # out until this transaction ends, so the snapshot stays exact.
                    await self._session.execute(
                        text("LOCK TABLE account_lifecycle_preferences IN SHARE ROW EXCLUSIVE MODE")
                    )
                rows = await self.list_all()
                if snapshot_token((_key(row), row.revision) for row in rows) != expected_snapshot:
                    await self._session.rollback()
                    return ReplaceOutcome.SNAPSHOT_MISMATCH, 0
                writes = _restore_writes({_key(row): row for row in rows}, targets)
                for write in writes:
                    if not await self._write(write):
                        await self._session.rollback()
                        return ReplaceOutcome.CONFLICT, 0
                await self._session.commit()
            except BaseException:
                await self._session.rollback()
                raise
        return ReplaceOutcome.APPLIED, len(writes)

    async def _write(self, write: LifecycleWrite) -> bool:
        now = utcnow()
        values = asdict(write.columns)
        if write.expected_revision == 0:
            insert_fn = _INSERT_FNS.get(self._session.get_bind().dialect.name)
            if insert_fn is None:
                raise RuntimeError("account_lifecycle_preferences writes support sqlite and postgresql only")
            statement = (
                insert_fn(AccountLifecyclePreference)
                .values(
                    account_id=write.key.account_id,
                    account_incarnation=write.key.account_incarnation,
                    revision=write.new_revision,
                    created_at=now,
                    updated_at=now,
                    **values,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        AccountLifecyclePreference.account_id,
                        AccountLifecyclePreference.account_incarnation,
                    ]
                )
                .returning(AccountLifecyclePreference.account_id)
            )
        else:
            statement = (
                update(AccountLifecyclePreference)
                .where(AccountLifecyclePreference.account_id == write.key.account_id)
                .where(AccountLifecyclePreference.account_incarnation == write.key.account_incarnation)
                .where(AccountLifecyclePreference.revision == write.expected_revision)
                .values(revision=write.new_revision, updated_at=now, **values)
                .returning(AccountLifecyclePreference.account_id)
            )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None


def _restore_writes(
    stored: Mapping[LifecycleKey, AccountLifecyclePreference],
    targets: Mapping[LifecycleKey, LifecycleTarget],
) -> list[LifecycleWrite]:
    writes: list[LifecycleWrite] = []
    for key in sorted({*stored, *targets}, key=lambda item: (item.account_id, item.account_incarnation)):
        row = stored.get(key)
        target = targets.get(key, LifecycleTarget(CLEARED_COLUMNS, 0))
        if row is None:
            writes.append(LifecycleWrite(key, target.columns, 0, target.recorded_revision + 1))
        elif LifecycleColumns.of(row) != target.columns or row.revision < target.recorded_revision:
            new_revision = max(row.revision, target.recorded_revision) + 1
            writes.append(LifecycleWrite(key, target.columns, row.revision, new_revision))
    if any(write.new_revision > MAX_STORED_REVISION for write in writes):
        raise LifecycleRevisionOverflowError(
            f"restoring would push a lifecycle revision past {MAX_STORED_REVISION}; nothing was written"
        )
    return writes
