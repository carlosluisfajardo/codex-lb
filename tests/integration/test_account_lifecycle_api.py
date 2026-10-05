from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

import app.core.auth.dependencies as auth_dependencies
from app.core.auth.dashboard_access import (
    DashboardPrincipal,
    DashboardRole,
    Permission,
    Scope,
    guest_principal,
    legacy_permissions,
)
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountLifecyclePreference, AccountStatus, DashboardSettings
from app.db.session import SessionLocal
from app.modules.account_lifecycle.backup import export_lifecycle_document
from app.modules.account_lifecycle.repository import account_incarnation
from app.modules.accounts.deletion import run_account_deletion_pass
from app.modules.accounts.repository import AccountsRepository

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]

_READ_IN_SUBPROCESS = """
import asyncio, sys
from app.db.session import SessionLocal, close_db
from app.modules.account_lifecycle.repository import AccountLifecycleRepository
from app.modules.account_lifecycle.service import AccountLifecycleService

async def main() -> None:
    async with SessionLocal() as session:
        result = await AccountLifecycleService(AccountLifecycleRepository(session)).get(sys.argv[1])
    await close_db()
    print(result.model_dump_json(by_alias=True) if result is not None else "null")

asyncio.run(main())
"""

_HELLO_ENDS_ON = {"precision": "date", "date": "2026-10-12", "time": None, "timezone": None}


@pytest.fixture(autouse=True)
def _no_background_deletion(monkeypatch):
    """Keep account deletion under explicit test control (see test_account_deletion_background)."""
    monkeypatch.setattr("app.modules.accounts.service.request_account_deletion_run", lambda: None)

    async def _no_tick(self) -> None:
        return None

    monkeypatch.setattr("app.modules.accounts.deletion.AccountDeletionScheduler._run_once", _no_tick)


def _account(account_id: str, email: str, *, routing_policy: str = "normal") -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type="plus",
        routing_policy=routing_policy,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


async def _insert(*accounts: Account) -> None:
    async with SessionLocal() as session:
        session.add_all(accounts)
        await session.commit()


def _payload(
    *,
    expected_revision: int,
    ends_on: dict[str, Any] | None = None,
    renews_on: dict[str, Any] | None = None,
    cancellation_status: str | None = None,
) -> dict[str, Any]:
    return {
        "endsOn": ends_on,
        "renewsOn": renews_on,
        "cancellationStatus": cancellation_status,
        "expectedRevision": expected_revision,
    }


_UNREADABLE_TOKEN = "0" * 64


async def _put(async_client, account_id: str, payload: dict[str, Any]):
    """PUT the way a dashboard draft read just before would: with the account's current concurrency token."""
    read = await async_client.get(f"/api/accounts/{account_id}/lifecycle")
    token = read.json()["concurrencyToken"] if read.status_code == 200 else _UNREADABLE_TOKEN
    return await async_client.put(
        f"/api/accounts/{account_id}/lifecycle", json={**payload, "expectedConcurrencyToken": token}
    )


def _read_in_subprocess(account_id: str) -> dict[str, Any] | None:
    completed = subprocess.run(
        [sys.executable, "-c", _READ_IN_SUBPROCESS, account_id],
        cwd=_REPO_ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


async def _incarnation(account_id: str) -> str:
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
    assert account is not None
    return account_incarnation(account.codex_installation_id)


async def _stored_rows(account_id: str) -> list[AccountLifecyclePreference]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(AccountLifecyclePreference).where(AccountLifecyclePreference.account_id == account_id)
        )
        return list(result.scalars().all())


async def _stored_row(account_id: str) -> AccountLifecyclePreference | None:
    rows = await _stored_rows(account_id)
    assert len(rows) <= 1
    return rows[0] if rows else None


@pytest.mark.asyncio
async def test_absent_saved_cleared_and_reloaded_from_a_separate_process(async_client):
    await _insert(_account("acc_fixture_hello", "hello@example.com"))

    absent = await async_client.get("/api/accounts/acc_fixture_hello/lifecycle")
    assert absent.status_code == 200
    token = absent.json()["concurrencyToken"]
    assert len(token) == 64
    assert absent.json() == {
        "accountId": "acc_fixture_hello",
        "endsOn": None,
        "renewsOn": None,
        "cancellationStatus": None,
        "revision": 0,
        "concurrencyToken": token,
        "updatedAt": None,
    }

    saved = await _put(
        async_client,
        "acc_fixture_hello",
        _payload(
            expected_revision=0,
            ends_on={"precision": "date", "date": "2026-10-12"},
            cancellation_status="cancelled",
        ),
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["endsOn"] == _HELLO_ENDS_ON
    assert body["renewsOn"] is None
    assert body["cancellationStatus"] == "cancelled"
    assert body["revision"] == 1
    assert body["updatedAt"] is not None

    reloaded = await async_client.get("/api/accounts/acc_fixture_hello/lifecycle")
    assert reloaded.json() == body
    assert _read_in_subprocess("acc_fixture_hello") == body

    cleared = await _put(
        async_client,
        "acc_fixture_hello",
        _payload(expected_revision=1),
    )
    assert cleared.status_code == 200, cleared.text
    cleared_body = cleared.json()
    assert cleared_body["endsOn"] is None
    assert cleared_body["renewsOn"] is None
    assert cleared_body["cancellationStatus"] is None
    assert cleared_body["revision"] == 2

    from_other_process = _read_in_subprocess("acc_fixture_hello")
    assert from_other_process is not None
    assert from_other_process["revision"] == 2
    assert from_other_process["endsOn"] is None
    assert from_other_process["cancellationStatus"] is None


@pytest.mark.asyncio
async def test_local_datetime_is_stored_and_returned_verbatim(async_client):
    await _insert(_account("acc_fixture_tz", "tz@example.com"))
    renews_on = {"precision": "datetime", "date": "2026-11-03", "time": "09:30", "timezone": "America/New_York"}
    ends_on = {"precision": "datetime", "date": "2026-10-12", "time": "23:59:59", "timezone": "+02:00"}

    response = await _put(
        async_client,
        "acc_fixture_tz",
        _payload(expected_revision=0, ends_on=ends_on, renews_on=renews_on),
    )

    assert response.status_code == 200, response.text
    assert response.json()["renewsOn"] == renews_on
    assert response.json()["endsOn"] == ends_on
    row = await _stored_row("acc_fixture_tz")
    assert row is not None
    assert (row.ends_on_date, row.ends_on_time, row.ends_on_timezone) == ("2026-10-12", "23:59:59", "+02:00")
    assert (row.renews_on_date, row.renews_on_time, row.renews_on_timezone) == (
        "2026-11-03",
        "09:30",
        "America/New_York",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ends_on",
    [
        {"precision": "date", "date": "2026-02-30"},
        {"precision": "date", "date": "2026-10-12", "time": "09:00"},
        {"precision": "datetime", "date": "2026-10-12", "time": "09:00"},
        {"precision": "datetime", "date": "2026-10-12", "time": "09:00", "timezone": "Mars/Olympus_Mons"},
    ],
)
async def test_invalid_values_are_rejected_and_change_nothing(async_client, ends_on):
    await _insert(_account("acc_fixture_invalid", "invalid@example.com"))
    first = await _put(
        async_client,
        "acc_fixture_invalid",
        _payload(expected_revision=0, ends_on={"precision": "date", "date": "2026-10-12"}),
    )
    assert first.status_code == 200

    response = await _put(
        async_client,
        "acc_fixture_invalid",
        _payload(expected_revision=1, ends_on=ends_on),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    unchanged = await async_client.get("/api/accounts/acc_fixture_invalid/lifecycle")
    assert unchanged.json()["revision"] == 1
    assert unchanged.json()["endsOn"] == _HELLO_ENDS_ON


@pytest.mark.asyncio
async def test_stale_and_concurrent_writes_are_rejected_with_conflict(async_client):
    await _insert(_account("acc_fixture_cas", "cas@example.com"))
    created = await _put(
        async_client,
        "acc_fixture_cas",
        _payload(expected_revision=0, cancellation_status="not_cancelled"),
    )
    assert created.json()["revision"] == 1

    for stale in (0, 2, 7):
        rejected = await _put(
            async_client,
            "acc_fixture_cas",
            _payload(expected_revision=stale, cancellation_status="cancelled"),
        )
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "account_lifecycle_conflict"

    responses = await asyncio.gather(
        _put(
            async_client,
            "acc_fixture_cas",
            _payload(expected_revision=1, ends_on={"precision": "date", "date": "2026-12-01"}),
        ),
        _put(
            async_client,
            "acc_fixture_cas",
            _payload(expected_revision=1, ends_on={"precision": "date", "date": "2027-01-01"}),
        ),
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response for response in responses if response.status_code == 200).json()
    assert winner["revision"] == 2
    final = (await async_client.get("/api/accounts/acc_fixture_cas/lifecycle")).json()
    assert final == winner


@pytest.mark.asyncio
async def test_first_write_race_on_an_absent_record_has_one_winner(async_client):
    await _insert(_account("acc_fixture_first", "first@example.com"))

    responses = await asyncio.gather(
        *(
            _put(
                async_client,
                "acc_fixture_first",
                _payload(expected_revision=0, ends_on={"precision": "date", "date": f"2026-10-1{day}"}),
            )
            for day in (1, 2)
        )
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert (await async_client.get("/api/accounts/acc_fixture_first/lifecycle")).json()["revision"] == 1


@pytest.mark.asyncio
async def test_metadata_is_keyed_by_exact_id_and_never_shared_by_email(async_client):
    await _insert(
        _account("acc_fixture_seat", "shared@example.com"),
        _account("acc_fixture_seat__copy2", "shared@example.com"),
    )
    saved = await _put(
        async_client,
        "acc_fixture_seat",
        _payload(expected_revision=0, ends_on={"precision": "date", "date": "2026-10-12"}),
    )
    assert saved.status_code == 200

    sibling = (await async_client.get("/api/accounts/acc_fixture_seat__copy2/lifecycle")).json()
    assert sibling["revision"] == 0
    assert sibling["endsOn"] is None

    sibling_saved = await _put(
        async_client,
        "acc_fixture_seat__copy2",
        _payload(expected_revision=0, cancellation_status="not_cancelled"),
    )
    assert sibling_saved.status_code == 200
    original = (await async_client.get("/api/accounts/acc_fixture_seat/lifecycle")).json()
    assert original == saved.json()


@pytest.mark.asyncio
async def test_side_by_side_reimport_gets_a_new_id_that_inherits_nothing(async_client):
    settings = await async_client.get("/api/settings")
    assert settings.json()["importWithoutOverwrite"] is True
    claims = {
        "email": "reenroll@example.com",
        "chatgpt_account_id": "acc_payload_reenroll",
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    raw = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    auth_json = {
        "tokens": {
            "idToken": f"header.{raw}.sig",
            "accessToken": "access",
            "refreshToken": "refresh",
            "accountId": "acc_fixture_reenroll",
        }
    }
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    imported = await async_client.post("/api/accounts/import", files=files)
    assert imported.status_code == 200, imported.text
    account_id = imported.json()["accountId"]
    saved = await _put(
        async_client,
        account_id,
        _payload(expected_revision=0, ends_on={"precision": "date", "date": "2026-10-12"}),
    )
    assert saved.status_code == 200

    reimported = await async_client.post("/api/accounts/import", files=files)

    assert reimported.status_code == 200, reimported.text
    new_id = reimported.json()["accountId"]
    assert new_id != account_id
    assert reimported.json()["email"] == imported.json()["email"]
    fresh = (await async_client.get(f"/api/accounts/{new_id}/lifecycle")).json()
    assert fresh["revision"] == 0
    assert fresh["endsOn"] is None
    assert (await async_client.get(f"/api/accounts/{account_id}/lifecycle")).json() == saved.json()


@pytest.mark.asyncio
async def test_overwrite_mode_reimport_keeps_the_row_and_its_metadata(async_client):
    settings = await async_client.put("/api/settings", json={"importWithoutOverwrite": False})
    assert settings.status_code == 200
    assert settings.json()["importWithoutOverwrite"] is False
    claims = {
        "email": "overwrite@example.com",
        "chatgpt_account_id": "acc_payload_overwrite",
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    raw = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    auth_json = {
        "tokens": {
            "idToken": f"header.{raw}.sig",
            "accessToken": "access",
            "refreshToken": "refresh",
            "accountId": "acc_fixture_overwrite",
        }
    }
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    imported = await async_client.post("/api/accounts/import", files=files)
    assert imported.status_code == 200, imported.text
    account_id = imported.json()["accountId"]
    saved = await _put(
        async_client,
        account_id,
        _payload(expected_revision=0, ends_on={"precision": "date", "date": "2026-10-12"}),
    )
    assert saved.status_code == 200

    reimported = await async_client.post("/api/accounts/import", files=files)

    assert reimported.status_code == 200, reimported.text
    assert reimported.json()["accountId"] == account_id
    assert (await async_client.get(f"/api/accounts/{account_id}/lifecycle")).json() == saved.json()


@pytest.mark.asyncio
async def test_in_place_reauthentication_keeps_the_id_and_its_metadata(async_client):
    await _insert(_account("acc_fixture_reauth", "reauth@example.com"))
    saved = await _put(
        async_client,
        "acc_fixture_reauth",
        _payload(expected_revision=0, renews_on={"precision": "date", "date": "2026-11-03"}),
    )
    assert saved.status_code == 200

    async with SessionLocal() as session:
        replaced = await AccountsRepository(session).replace_reauthorized(
            "acc_fixture_reauth",
            _account("acc_fixture_reauth_material", "reauth@example.com"),
        )

    assert replaced is not None and replaced.id == "acc_fixture_reauth"
    assert (await async_client.get("/api/accounts/acc_fixture_reauth/lifecycle")).json() == saved.json()


@pytest.mark.asyncio
async def test_unknown_and_pending_deletion_accounts_are_not_found(async_client):
    missing = await async_client.get("/api/accounts/acc_fixture_missing/lifecycle")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "account_not_found"
    missing_write = await _put(async_client, "acc_fixture_missing", _payload(expected_revision=0))
    assert missing_write.status_code == 404
    assert await _stored_row("acc_fixture_missing") is None


@pytest.mark.asyncio
async def test_deleted_account_notes_are_retained_but_never_shown_for_a_recreated_id(async_client):
    await _insert(_account("acc_fixture_gone", "gone@example.com"))
    saved = await _put(
        async_client,
        "acc_fixture_gone",
        _payload(expected_revision=0, ends_on={"precision": "date", "date": "2026-10-12"}),
    )
    assert saved.status_code == 200

    deleted = await async_client.delete("/api/accounts/acc_fixture_gone")
    assert deleted.status_code == 200
    pending = await async_client.get("/api/accounts/acc_fixture_gone/lifecycle")
    assert pending.status_code == 404
    pending_write = await _put(async_client, "acc_fixture_gone", _payload(expected_revision=1))
    assert pending_write.status_code == 404

    assert await run_account_deletion_pass(batch_size=10) == {"acc_fixture_gone": "finalized"}
    async with SessionLocal() as session:
        assert await session.get(Account, "acc_fixture_gone") is None
    orphans = await _stored_rows("acc_fixture_gone")
    assert [(row.ends_on_date, row.revision) for row in orphans] == [("2026-10-12", 1)]

    await _insert(_account("acc_fixture_gone", "someone-else@example.com"))
    recreated = await async_client.get("/api/accounts/acc_fixture_gone/lifecycle")
    assert recreated.status_code == 200
    assert recreated.json()["revision"] == 0
    assert recreated.json()["endsOn"] is None

    own = await _put(
        async_client,
        "acc_fixture_gone",
        _payload(expected_revision=0, cancellation_status="not_cancelled"),
    )
    assert own.status_code == 200, own.text
    assert own.json()["revision"] == 1
    assert own.json()["endsOn"] is None
    rows = await _stored_rows("acc_fixture_gone")
    assert {(row.ends_on_date, row.cancellation_status, row.revision) for row in rows} == {
        (None, "not_cancelled", 1),
        ("2026-10-12", None, 1),
    }


@pytest.mark.asyncio
async def test_superseding_a_pending_deletion_in_place_keeps_the_notes(async_client):
    await _insert(_account("acc_fixture_back", "back@example.com"))
    saved = await _put(
        async_client,
        "acc_fixture_back",
        _payload(expected_revision=0, ends_on={"precision": "date", "date": "2026-10-12"}),
    )
    assert saved.status_code == 200
    assert (await async_client.delete("/api/accounts/acc_fixture_back")).status_code == 200
    assert (await async_client.get("/api/accounts/acc_fixture_back/lifecycle")).status_code == 404

    async with SessionLocal() as session:
        replaced = await AccountsRepository(session).replace_reauthorized(
            "acc_fixture_back",
            _account("acc_fixture_back_material", "back@example.com"),
        )
    assert replaced is not None

    assert (await async_client.get("/api/accounts/acc_fixture_back/lifecycle")).json() == saved.json()


@pytest.mark.asyncio
async def test_marking_cancelled_leaves_the_dates_as_entered(async_client):
    await _insert(_account("acc_fixture_cancel", "cancel@example.com"))
    renews_on = {"precision": "date", "date": "2026-11-03", "time": None, "timezone": None}
    seeded = await _put(
        async_client,
        "acc_fixture_cancel",
        _payload(expected_revision=0, renews_on={"precision": "date", "date": "2026-11-03"}),
    )
    assert seeded.json()["revision"] == 1

    cancelled = await _put(
        async_client,
        "acc_fixture_cancel",
        _payload(
            expected_revision=1,
            renews_on={"precision": "date", "date": "2026-11-03"},
            cancellation_status="cancelled",
        ),
    )

    assert cancelled.status_code == 200
    assert cancelled.json()["renewsOn"] == renews_on
    assert cancelled.json()["endsOn"] is None
    assert cancelled.json()["cancellationStatus"] == "cancelled"
    assert cancelled.json()["revision"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("expected_revision", [2**31 - 1, 2**63])
async def test_out_of_range_revisions_are_rejected_as_invalid(async_client, expected_revision):
    await _insert(_account("acc_fixture_range", "range@example.com"))

    response = await _put(async_client, "acc_fixture_range", _payload(expected_revision=expected_revision))

    assert response.status_code == 422
    assert await _stored_rows("acc_fixture_range") == []


@pytest.mark.asyncio
async def test_the_largest_storable_revision_can_still_be_read_and_exported(async_client):
    await _insert(_account("acc_fixture_ceiling", "ceiling@example.com"))
    async with SessionLocal() as session:
        session.add(
            AccountLifecyclePreference(
                account_id="acc_fixture_ceiling",
                account_incarnation=await _incarnation("acc_fixture_ceiling"),
                revision=2**31 - 2,
            )
        )
        await session.commit()

    response = await _put(
        async_client,
        "acc_fixture_ceiling",
        _payload(expected_revision=2**31 - 2, ends_on={"precision": "date", "date": "2026-10-12"}),
    )

    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 2**31 - 1
    async with SessionLocal() as session:
        document = await export_lifecycle_document(session)
    assert [entry.revision for entry in document.entries] == [2**31 - 1]


@pytest.mark.asyncio
async def test_non_ascii_digits_are_rejected(async_client):
    await _insert(_account("acc_fixture_digits", "digits@example.com"))
    ends_on = {"precision": "datetime", "date": "2026-10-12", "time": "0\u0669:3\u0660", "timezone": "+05:30"}

    response = await _put(async_client, "acc_fixture_digits", _payload(expected_revision=0, ends_on=ends_on))

    assert response.status_code == 422
    assert await _stored_rows("acc_fixture_digits") == []


@pytest.mark.asyncio
async def test_saving_lifecycle_touches_neither_routing_policy_nor_settings_version(async_client):
    await _insert(_account("acc_fixture_burn", "burn@example.com", routing_policy="burn_first"))
    settings_before = await async_client.get("/api/settings")
    assert settings_before.status_code == 200

    response = await _put(
        async_client,
        "acc_fixture_burn",
        _payload(
            expected_revision=0,
            ends_on={"precision": "date", "date": "2026-10-12"},
            cancellation_status="cancelled",
        ),
    )

    assert response.status_code == 200
    accounts = (await async_client.get("/api/accounts")).json()["accounts"]
    assert next(a for a in accounts if a["accountId"] == "acc_fixture_burn")["routingPolicy"] == "burn_first"
    async with SessionLocal() as session:
        versions = (await session.execute(select(DashboardSettings.version))).scalars().all()
    settings_after = await async_client.get("/api/settings")
    assert settings_after.json()["version"] == settings_before.json()["version"]
    assert versions == [settings_before.json()["version"]]


_ORIGINAL_VALIDATE_DASHBOARD_SESSION = auth_dependencies.validate_dashboard_session


def _use_principal(app_instance, monkeypatch, principal: DashboardPrincipal) -> None:
    """Same override technique as test_dashboard_permission_gates.py."""
    monkeypatch.setattr(auth_dependencies, "validate_dashboard_session", AsyncMock(return_value=principal))
    monkeypatch.setitem(app_instance.dependency_overrides, _ORIGINAL_VALIDATE_DASHBOARD_SESSION, lambda: principal)


@pytest.mark.asyncio
async def test_viewer_reads_but_cannot_write(app_instance, async_client, monkeypatch):
    await _insert(_account("acc_fixture_viewer", "viewer@example.com"))
    saved = await _put(
        async_client,
        "acc_fixture_viewer",
        _payload(expected_revision=0, ends_on={"precision": "date", "date": "2026-10-12"}),
    )
    assert saved.status_code == 200
    _use_principal(app_instance, monkeypatch, guest_principal())

    read = await async_client.get("/api/accounts/acc_fixture_viewer/lifecycle")
    write = await _put(async_client, "acc_fixture_viewer", _payload(expected_revision=1))

    assert read.status_code == 200
    assert read.json() == saved.json()
    assert write.status_code == 403
    assert write.json()["error"]["code"] == "permission_required"
    assert write.json()["error"]["param"] == "accounts:write"
    row = await _stored_row("acc_fixture_viewer")
    assert row is not None and row.revision == 1 and row.ends_on_date == "2026-10-12"


@pytest.mark.asyncio
async def test_principal_without_accounts_read_cannot_read(app_instance, async_client, monkeypatch):
    await _insert(_account("acc_fixture_member", "member@example.com"))
    grants = {Permission.DASHBOARD_READ: Scope.OWN}
    member = DashboardPrincipal(
        role=DashboardRole.ADMIN,
        permissions=legacy_permissions(grants),
        auth_mode=DashboardAuthMode.STANDARD,
        grants=grants,
    )
    _use_principal(app_instance, monkeypatch, member)

    response = await async_client.get("/api/accounts/acc_fixture_member/lifecycle")

    assert response.status_code == 403
    assert response.json()["error"]["param"] == "accounts:read"
