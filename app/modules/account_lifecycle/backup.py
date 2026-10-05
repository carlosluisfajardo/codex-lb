"""Private, host-local backup of the lifecycle namespace: lifecycle fields only, never identity or tokens."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import utcnow
from app.modules.account_lifecycle.repository import (
    AccountLifecycleRepository,
    LifecycleKey,
    LifecycleTarget,
    ReplaceOutcome,
    snapshot_token,
)
from app.modules.account_lifecycle.schemas import MAX_STORED_REVISION, CancellationStatus, LifecycleDate
from app.modules.account_lifecycle.service import (
    AccountLifecycleConflictError,
    lifecycle_columns,
    stored_cancellation_status,
    stored_lifecycle_date,
)
from app.modules.shared.schemas import DashboardModel

FORMAT_NAME = "codex-lb/account-lifecycle-preferences"
FORMAT_VERSION = 1


class LifecycleSnapshotMismatchError(Exception):
    """The stored namespace no longer matches the snapshot the operator restored against."""


class UnsupportedBackupFormatError(ValueError):
    """The document is not a lifecycle backup this build can apply exactly."""


class LifecycleBackupEntry(DashboardModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1)
    account_incarnation: str = Field(pattern=r"^[0-9a-f]{64}$")
    account_present: bool
    ends_on: LifecycleDate | None
    renews_on: LifecycleDate | None
    cancellation_status: CancellationStatus | None
    revision: int = Field(ge=1, le=MAX_STORED_REVISION)
    updated_at: datetime


class LifecycleBackupDocument(DashboardModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["codex-lb/account-lifecycle-preferences"]
    format_version: Literal[1]
    exported_at: datetime
    snapshot: str
    entries: list[LifecycleBackupEntry]

    @model_validator(mode="after")
    def _unique_records(self) -> LifecycleBackupDocument:
        seen: set[tuple[str, str]] = set()
        for entry in self.entries:
            record = (entry.account_id, entry.account_incarnation)
            if record in seen:
                raise ValueError(f"duplicate record for account id {entry.account_id!r}")
            seen.add(record)
        return self


async def export_lifecycle_document(session: AsyncSession) -> LifecycleBackupDocument:
    repository = AccountLifecycleRepository(session)
    rows = await repository.list_all()
    present = await repository.visible_account_incarnations({row.account_id for row in rows})
    return LifecycleBackupDocument(
        format=FORMAT_NAME,
        format_version=FORMAT_VERSION,
        exported_at=utcnow(),
        snapshot=snapshot_token((LifecycleKey(row.account_id, row.account_incarnation), row.revision) for row in rows),
        entries=[
            LifecycleBackupEntry(
                account_id=row.account_id,
                account_incarnation=row.account_incarnation,
                account_present=present.get(row.account_id) == row.account_incarnation,
                ends_on=stored_lifecycle_date(row.ends_on_date, row.ends_on_time, row.ends_on_timezone),
                renews_on=stored_lifecycle_date(row.renews_on_date, row.renews_on_time, row.renews_on_timezone),
                cancellation_status=stored_cancellation_status(row.cancellation_status),
                revision=row.revision,
                updated_at=row.updated_at,
            )
            for row in rows
        ],
    )


async def restore_lifecycle_document(
    session: AsyncSession,
    document: LifecycleBackupDocument,
    *,
    expected_snapshot: str,
) -> int:
    """Make the stored namespace equal ``document``; returns how many records were written."""

    targets = {
        LifecycleKey(entry.account_id, entry.account_incarnation): LifecycleTarget(
            columns=lifecycle_columns(entry.ends_on, entry.renews_on, entry.cancellation_status),
            recorded_revision=entry.revision,
        )
        for entry in document.entries
    }
    outcome, written = await AccountLifecycleRepository(session).replace_all(
        targets,
        expected_snapshot=expected_snapshot,
    )
    if outcome is ReplaceOutcome.SNAPSHOT_MISMATCH:
        raise LifecycleSnapshotMismatchError(
            "the stored lifecycle preferences no longer match --expected-snapshot; export again and use its snapshot"
        )
    if outcome is ReplaceOutcome.CONFLICT:
        raise AccountLifecycleConflictError("a lifecycle record changed during the restore; nothing was written")
    return written


def write_private_document(path: Path, document: LifecycleBackupDocument) -> None:
    """Write ``document`` owner-only and publish it complete; an existing ``path`` is never replaced."""

    content = document.model_dump_json(by_alias=True, indent=2) + "\n"
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        os.unlink(temporary)
    if os.name == "posix":
        # Make the new directory entry durable; other platforms cannot open a directory this way.
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def read_document(path: Path) -> LifecycleBackupDocument:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("format") != FORMAT_NAME or raw.get("formatVersion") != FORMAT_VERSION:
        raise UnsupportedBackupFormatError(
            f"unsupported backup format: expected format {FORMAT_NAME!r} with formatVersion {FORMAT_VERSION}"
        )
    return LifecycleBackupDocument.model_validate(raw)
