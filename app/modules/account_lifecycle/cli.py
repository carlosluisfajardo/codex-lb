"""``codex-lb account-lifecycle``: private export and compare-and-set restore of lifecycle preferences.

Uses the configured database directly and never starts the server, so it also works while the
server is stopped (for example around a package rollback). Output names counts and the snapshot
token only; lifecycle values stay in the private file.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import DatabaseError

from app.modules.account_lifecycle.backup import (
    LifecycleSnapshotMismatchError,
    UnsupportedBackupFormatError,
    export_lifecycle_document,
    read_document,
    restore_lifecycle_document,
    write_private_document,
)
from app.modules.account_lifecycle.repository import LifecycleRevisionOverflowError
from app.modules.account_lifecycle.service import AccountLifecycleConflictError

_SCHEMA_HINT = "Run `codex-lb-db upgrade` (or start the server once) so the lifecycle table exists, then retry."


def run_account_lifecycle_command(args: argparse.Namespace) -> None:
    command = getattr(args, "account_lifecycle_command", None)
    try:
        if command == "export":
            asyncio.run(_export(args.output))
        elif command == "restore":
            asyncio.run(_restore(args.input, args.expected_snapshot))
        else:
            raise SystemExit("account-lifecycle requires a subcommand: export, restore")
    except DatabaseError as exc:
        raise SystemExit(f"Database error: {exc.orig}. {_SCHEMA_HINT}") from exc


async def _export(output: Path) -> None:
    from app.db.session import SessionLocal, close_db

    try:
        async with SessionLocal() as session:
            document = await export_lifecycle_document(session)
    finally:
        await close_db()
    try:
        write_private_document(output, document)
    except FileExistsError as exc:
        raise SystemExit(f"{output} already exists; choose a new path so no earlier backup is replaced.") from exc
    except OSError as exc:
        raise SystemExit(f"Unable to write {output}: {exc}") from exc
    orphans = sum(1 for entry in document.entries if not entry.account_present)
    print(f"Exported {len(document.entries)} lifecycle record(s) ({orphans} without a present account) to {output}")
    print(f"snapshot={document.snapshot}")


async def _restore(source: Path, expected_snapshot: str) -> None:
    from app.db.session import SessionLocal, close_db

    try:
        document = read_document(source)
    except UnsupportedBackupFormatError as exc:
        raise SystemExit(str(exc)) from exc
    except ValidationError as exc:
        raise SystemExit(f"invalid backup document; nothing was written:\n{exc}") from exc
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Unable to read {source}: {exc}") from exc
    try:
        async with SessionLocal() as session:
            written = await restore_lifecycle_document(session, document, expected_snapshot=expected_snapshot)
            after = await export_lifecycle_document(session)
    except (LifecycleSnapshotMismatchError, AccountLifecycleConflictError, LifecycleRevisionOverflowError) as exc:
        raise SystemExit(f"{exc}") from exc
    finally:
        await close_db()
    print(f"Restored {len(document.entries)} lifecycle record(s) from {source}; {written} record(s) written")
    print(f"snapshot={after.snapshot}")
