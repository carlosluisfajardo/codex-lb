from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountLifecyclePreference, AccountStatus
from app.db.session import SessionLocal
from app.modules.account_lifecycle import backup as backup_module
from app.modules.account_lifecycle.backup import (
    FORMAT_NAME,
    export_lifecycle_document,
    write_private_document,
)
from app.modules.account_lifecycle.repository import AccountLifecycleRepository
from app.modules.account_lifecycle.service import AccountLifecycleConflictError

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENTRY_KEYS = {
    "accountId",
    "accountIncarnation",
    "accountPresent",
    "endsOn",
    "renewsOn",
    "cancellationStatus",
    "revision",
    "updatedAt",
}
_ORPHAN_INCARNATION = "0" * 64


def _account(account_id: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("secret-access-token"),
        refresh_token_encrypted=encryptor.encrypt("secret-refresh-token"),
        id_token_encrypted=encryptor.encrypt("secret-id-token"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


async def _seed(async_client) -> None:
    async with SessionLocal() as session:
        session.add_all([_account("acc_fixture_a"), _account("acc_fixture_b")])
        session.add(
            AccountLifecyclePreference(
                account_id="acc_fixture_orphan",
                account_incarnation=_ORPHAN_INCARNATION,
                ends_on_date="2026-09-30",
                cancellation_status="cancelled",
                revision=1,
            )
        )
        await session.commit()
    for account_id, body in (
        ("acc_fixture_a", {"endsOn": {"precision": "date", "date": "2026-10-12"}, "cancellationStatus": "cancelled"}),
        (
            "acc_fixture_b",
            {
                "renewsOn": {
                    "precision": "datetime",
                    "date": "2026-11-03",
                    "time": "09:30",
                    "timezone": "America/New_York",
                }
            },
        ),
    ):
        response = await _put(async_client, account_id, _body(0, **body))
        assert response.status_code == 200, response.text


def _body(expected_revision: int, **fields: Any) -> dict[str, Any]:
    return {
        "endsOn": fields.get("endsOn"),
        "renewsOn": fields.get("renewsOn"),
        "cancellationStatus": fields.get("cancellationStatus"),
        "expectedRevision": expected_revision,
    }


async def _put(async_client, account_id: str, payload: dict[str, Any]):
    """PUT the way a dashboard draft read just before would: with the account's current concurrency token."""
    read = await async_client.get(f"/api/accounts/{account_id}/lifecycle")
    assert read.status_code == 200, read.text
    return await async_client.put(
        f"/api/accounts/{account_id}/lifecycle",
        json={**payload, "expectedConcurrencyToken": read.json()["concurrencyToken"]},
    )


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.cli", "account-lifecycle", *args],
        cwd=_REPO_ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


async def _current_document() -> dict[str, Any]:
    async with SessionLocal() as session:
        document = await export_lifecycle_document(session)
    return document.model_dump(mode="json", by_alias=True)


def _entries(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["accountId"]: entry for entry in document["entries"]}


def _fields(entry: dict[str, Any]) -> tuple[Any, Any, Any]:
    return entry["endsOn"], entry["renewsOn"], entry["cancellationStatus"]


@pytest.mark.asyncio
async def test_export_writes_a_private_complete_file_with_only_lifecycle_fields(async_client, tmp_path):
    await _seed(async_client)
    target = tmp_path / "lifecycle-backup.json"

    completed = _cli("export", "--output", str(target))

    assert completed.returncode == 0, completed.stderr
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert sorted(path.name for path in tmp_path.iterdir()) == ["lifecycle-backup.json"]
    content = target.read_text()
    document = json.loads(content)
    assert set(document) == {"format", "formatVersion", "exportedAt", "snapshot", "entries"}
    assert document["format"] == FORMAT_NAME
    assert document["formatVersion"] == 1
    assert document["snapshot"] in completed.stdout
    assert [entry["accountId"] for entry in document["entries"]] == [
        "acc_fixture_a",
        "acc_fixture_b",
        "acc_fixture_orphan",
    ]
    assert all(set(entry) == _ENTRY_KEYS for entry in document["entries"])
    entries = _entries(document)
    assert entries["acc_fixture_orphan"]["accountPresent"] is False
    assert entries["acc_fixture_orphan"]["accountIncarnation"] == _ORPHAN_INCARNATION
    assert len(entries["acc_fixture_a"]["accountIncarnation"]) == 64
    assert entries["acc_fixture_a"]["accountPresent"] is True
    assert entries["acc_fixture_a"]["endsOn"] == {
        "precision": "date",
        "date": "2026-10-12",
        "time": None,
        "timezone": None,
    }
    assert entries["acc_fixture_b"]["renewsOn"]["timezone"] == "America/New_York"
    for secret_or_identity in ("example.com", "secret-", "access_token", "refresh", "email"):
        assert secret_or_identity not in content


@pytest.mark.asyncio
async def test_export_never_replaces_an_existing_file(async_client, tmp_path):
    await _seed(async_client)
    target = tmp_path / "lifecycle-backup.json"
    target.write_text("previous backup")
    target.chmod(0o600)

    completed = _cli("export", "--output", str(target))

    assert completed.returncode != 0
    assert "exists" in completed.stderr
    assert target.read_text() == "previous backup"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["lifecycle-backup.json"]


@pytest.mark.asyncio
async def test_interrupted_write_leaves_neither_target_nor_temporary_file(db_setup, tmp_path, monkeypatch):
    async with SessionLocal() as session:
        document = await export_lifecycle_document(session)
    target = tmp_path / "lifecycle-backup.json"

    def _fail_link(src: str, dst: str) -> None:
        raise OSError("simulated crash before publish")

    monkeypatch.setattr(backup_module.os, "link", _fail_link)

    with pytest.raises(OSError, match="simulated crash"):
        write_private_document(target, document)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_restore_round_trips_a_backup_without_lowering_revisions(async_client, tmp_path):
    await _seed(async_client)
    backup = tmp_path / "before.json"
    assert _cli("export", "--output", str(backup)).returncode == 0
    exported = json.loads(backup.read_text())

    assert (await _put(async_client, "acc_fixture_a", _body(1))).status_code == 200
    changed_b = _body(1, renewsOn={"precision": "date", "date": "2027-11-03"}, cancellationStatus="not_cancelled")
    assert (await _put(async_client, "acc_fixture_b", changed_b)).status_code == 200
    async with SessionLocal() as session:
        session.add(_account("acc_fixture_c"))
        await session.commit()
    added_c = _body(0, endsOn={"precision": "date", "date": "2026-12-31"})
    assert (await _put(async_client, "acc_fixture_c", added_c)).status_code == 200
    current = await _current_document()

    completed = _cli("restore", "--input", str(backup), "--expected-snapshot", current["snapshot"])

    assert completed.returncode == 0, completed.stderr
    restored = _entries(await _current_document())
    before = _entries(exported)
    for account_id in ("acc_fixture_a", "acc_fixture_b", "acc_fixture_orphan"):
        assert _fields(restored[account_id]) == _fields(before[account_id])
    assert _fields(restored["acc_fixture_c"]) == (None, None, None)
    revisions_before_restore = {entry["accountId"]: entry["revision"] for entry in current["entries"]}
    assert restored["acc_fixture_a"]["revision"] == revisions_before_restore["acc_fixture_a"] + 1
    assert restored["acc_fixture_b"]["revision"] == revisions_before_restore["acc_fixture_b"] + 1
    assert restored["acc_fixture_c"]["revision"] == revisions_before_restore["acc_fixture_c"] + 1
    assert restored["acc_fixture_orphan"]["revision"] == revisions_before_restore["acc_fixture_orphan"]
    api_view = (await async_client.get("/api/accounts/acc_fixture_a/lifecycle")).json()
    assert api_view["endsOn"] == before["acc_fixture_a"]["endsOn"]


@pytest.mark.asyncio
async def test_restore_with_a_stale_snapshot_changes_nothing(async_client, tmp_path):
    await _seed(async_client)
    backup = tmp_path / "before.json"
    assert _cli("export", "--output", str(backup)).returncode == 0
    stale_snapshot = json.loads(backup.read_text())["snapshot"]
    assert (await _put(async_client, "acc_fixture_a", _body(1))).status_code == 200
    current = await _current_document()

    completed = _cli("restore", "--input", str(backup), "--expected-snapshot", stale_snapshot)

    assert completed.returncode != 0
    assert "snapshot" in completed.stderr
    after = await _current_document()
    assert after["entries"] == current["entries"]
    assert after["snapshot"] == current["snapshot"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda document: document.update(formatVersion=2), "format"),
        (lambda document: document.update(format="something-else"), "format"),
        (lambda document: document["entries"][0]["endsOn"].update(date="2026-02-30"), "invalid"),
        (lambda document: document["entries"].append(dict(document["entries"][0])), "duplicate"),
    ],
)
async def test_restore_rejects_documents_it_cannot_apply_exactly(async_client, tmp_path, mutate, message):
    await _seed(async_client)
    backup = tmp_path / "before.json"
    assert _cli("export", "--output", str(backup)).returncode == 0
    document = json.loads(backup.read_text())
    mutate(document)
    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps(document))
    current = await _current_document()

    completed = _cli("restore", "--input", str(edited), "--expected-snapshot", current["snapshot"])

    assert completed.returncode != 0
    assert message in completed.stderr.lower()
    assert (await _current_document())["entries"] == current["entries"]


@pytest.mark.asyncio
async def test_restore_after_a_schema_rollback_never_reuses_a_revision(async_client, tmp_path):
    async with SessionLocal() as session:
        session.add(_account("acc_fixture_reuse"))
        await session.commit()
    for revision, day in ((0, "01"), (1, "02"), (2, "03")):
        body = _body(revision, endsOn={"precision": "date", "date": f"2026-10-{day}"})
        assert (await _put(async_client, "acc_fixture_reuse", body)).status_code == 200
    backup = tmp_path / "before-downgrade.json"
    assert _cli("export", "--output", str(backup)).returncode == 0
    assert _entries(json.loads(backup.read_text()))["acc_fixture_reuse"]["revision"] == 3
    async with SessionLocal() as session:
        await session.execute(delete(AccountLifecyclePreference))
        await session.commit()
    empty = await _current_document()
    assert empty["entries"] == []

    completed = _cli("restore", "--input", str(backup), "--expected-snapshot", empty["snapshot"])

    assert completed.returncode == 0, completed.stderr
    restored = (await async_client.get("/api/accounts/acc_fixture_reuse/lifecycle")).json()
    assert restored["endsOn"]["date"] == "2026-10-03"
    assert restored["revision"] > 3
    for stale in (1, 2, 3):
        stale_write = await _put(async_client, "acc_fixture_reuse", _body(stale))
        assert stale_write.status_code == 409
    fresh_write = await _put(async_client, "acc_fixture_reuse", _body(restored["revision"]))
    assert fresh_write.status_code == 200


@pytest.mark.asyncio
async def test_restored_records_of_removed_accounts_stay_hidden(async_client, tmp_path):
    await _seed(async_client)
    backup = tmp_path / "before.json"
    assert _cli("export", "--output", str(backup)).returncode == 0
    current = await _current_document()

    completed = _cli("restore", "--input", str(backup), "--expected-snapshot", current["snapshot"])

    assert completed.returncode == 0, completed.stderr
    async with SessionLocal() as session:
        session.add(_account("acc_fixture_orphan"))
        await session.commit()
    recreated = (await async_client.get("/api/accounts/acc_fixture_orphan/lifecycle")).json()
    assert recreated["revision"] == 0
    assert recreated["endsOn"] is None
    assert _entries(await _current_document())["acc_fixture_orphan"]["accountPresent"] is False


@pytest.mark.asyncio
async def test_restore_refuses_a_revision_beyond_the_column_range(async_client, tmp_path):
    await _seed(async_client)
    backup = tmp_path / "before.json"
    assert _cli("export", "--output", str(backup)).returncode == 0
    document = json.loads(backup.read_text())
    document["entries"].append({**document["entries"][0], "accountId": "acc_fixture_ceiling", "revision": 2**31 - 1})
    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps(document))
    current = await _current_document()

    completed = _cli("restore", "--input", str(edited), "--expected-snapshot", current["snapshot"])

    assert completed.returncode != 0
    assert "revision" in completed.stderr.lower()
    assert (await _current_document())["entries"] == current["entries"]


@pytest.mark.asyncio
async def test_export_succeeds_where_directories_cannot_be_opened(db_setup, tmp_path, monkeypatch):
    async with SessionLocal() as session:
        document = await export_lifecycle_document(session)
    target = tmp_path / "lifecycle-backup.json"
    real_open = os.open

    def _no_directory_open(path, flags, *args, **kwargs):
        if os.path.isdir(path):
            raise PermissionError("directories cannot be opened on this platform")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(backup_module.os, "name", "nt")
    monkeypatch.setattr(backup_module.os, "open", _no_directory_open)

    write_private_document(target, document)

    assert json.loads(target.read_text())["snapshot"] == document.snapshot
    assert sorted(path.name for path in tmp_path.iterdir()) == ["lifecycle-backup.json"]


@pytest.mark.asyncio
async def test_a_record_write_that_misses_its_revision_rolls_back_the_whole_restore(
    async_client, tmp_path, monkeypatch
):
    # The SQLite write fence keeps other writers out of the restore window (see
    # test_account_lifecycle_restore_fence.py); a revision miss can still happen on PostgreSQL paths,
    # so the restore must stay all-or-nothing when one record's conditional write matches nothing.
    await _seed(async_client)
    backup = tmp_path / "before.json"
    assert _cli("export", "--output", str(backup)).returncode == 0
    assert (await _put(async_client, "acc_fixture_a", _body(1))).status_code == 200
    assert (await _put(async_client, "acc_fixture_b", _body(1))).status_code == 200
    current = await _current_document()
    document = backup_module.read_document(backup)
    original_write = AccountLifecycleRepository._write

    async def _miss_on_acc_fixture_b(self, write):
        # acc_fixture_a is written first by the restore; acc_fixture_b's write then matches nothing.
        if write.key.account_id == "acc_fixture_b":
            return False
        return await original_write(self, write)

    monkeypatch.setattr(AccountLifecycleRepository, "_write", _miss_on_acc_fixture_b)

    async with SessionLocal() as session:
        with pytest.raises(AccountLifecycleConflictError):
            await backup_module.restore_lifecycle_document(session, document, expected_snapshot=current["snapshot"])

    monkeypatch.setattr(AccountLifecycleRepository, "_write", original_write)
    after = await _current_document()
    assert after["entries"] == current["entries"]
    assert after["snapshot"] == current["snapshot"]
