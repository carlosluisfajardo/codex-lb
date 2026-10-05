"""A lifecycle write is bound to the account row (incarnation) its draft was read from."""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from sqlalchemy import delete, select, update

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountLifecyclePreference, AccountStatus
from app.db.session import SessionLocal
from app.modules.account_lifecycle.repository import (
    AccountLifecycleRepository,
    LifecycleColumns,
    LifecycleKey,
    account_incarnation,
)
from app.modules.accounts.repository import AccountsRepository

pytestmark = pytest.mark.integration

_TOKEN = re.compile(r"[0-9a-f]{64}")
_ID = "acc_fixture_recycled"


def _account(account_id: str = _ID, *, installation_id: str | None = None) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        codex_installation_id=installation_id or str(uuid.uuid4()),
        email="recycled@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
    )


async def _insert(account: Account) -> None:
    async with SessionLocal() as session:
        session.add(account)
        await session.commit()


async def _recycle(account_id: str = _ID) -> None:
    """Finalize the row's removal and create a new row that is given the same exact id."""
    async with SessionLocal() as session:
        await session.execute(delete(Account).where(Account.id == account_id))
        await session.commit()
    await _insert(_account(account_id))


def _body(*, revision: int, token: str | None, ends_on: str | None = None, cancelled: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {
        "endsOn": {"precision": "date", "date": ends_on} if ends_on else None,
        "renewsOn": None,
        "cancellationStatus": "cancelled" if cancelled else None,
        "expectedRevision": revision,
    }
    if token is not None:
        body["expectedConcurrencyToken"] = token
    return body


async def _rows(account_id: str = _ID) -> list[tuple[str, str | None, int]]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(AccountLifecyclePreference).where(AccountLifecyclePreference.account_id == account_id)
        )
        return sorted((row.account_incarnation, row.ends_on_date, row.revision) for row in result.scalars())


@pytest.mark.asyncio
async def test_every_read_returns_an_incarnation_bound_token_including_revision_zero(async_client):
    installation_id = str(uuid.uuid4())
    await _insert(_account(installation_id=installation_id))

    empty = (await async_client.get(f"/api/accounts/{_ID}/lifecycle")).json()
    token = empty["concurrencyToken"]

    assert empty["revision"] == 0
    assert _TOKEN.fullmatch(token)
    assert installation_id not in token
    assert token != account_incarnation(installation_id)
    saved = await async_client.put(
        f"/api/accounts/{_ID}/lifecycle", json=_body(revision=0, token=token, ends_on="2026-10-12")
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["concurrencyToken"] == token
    async with SessionLocal() as session:
        replaced = await AccountsRepository(session).replace_reauthorized(_ID, _account(installation_id=None))
    assert replaced is not None and replaced.codex_installation_id == installation_id
    assert (await async_client.get(f"/api/accounts/{_ID}/lifecycle")).json()["concurrencyToken"] == token

    await _recycle()

    recycled = (await async_client.get(f"/api/accounts/{_ID}/lifecycle")).json()
    assert recycled["revision"] == 0
    assert _TOKEN.fullmatch(recycled["concurrencyToken"])
    assert recycled["concurrencyToken"] != token


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", [0, 1])
async def test_a_draft_read_before_the_id_was_recycled_conflicts_at_an_equal_revision(async_client, revision):
    await _insert(_account())
    if revision:
        first = (await async_client.get(f"/api/accounts/{_ID}/lifecycle")).json()["concurrencyToken"]
        body = _body(revision=0, token=first, ends_on="2026-10-31")
        assert (await async_client.put(f"/api/accounts/{_ID}/lifecycle", json=body)).status_code == 200
    stale = (await async_client.get(f"/api/accounts/{_ID}/lifecycle")).json()
    assert stale["revision"] == revision

    await _recycle()
    if revision:
        fresh_token = (await async_client.get(f"/api/accounts/{_ID}/lifecycle")).json()["concurrencyToken"]
        body = _body(revision=0, token=fresh_token, ends_on="2026-10-30")
        assert (await async_client.put(f"/api/accounts/{_ID}/lifecycle", json=body)).status_code == 200
    current = (await async_client.get(f"/api/accounts/{_ID}/lifecycle")).json()
    assert current["revision"] == stale["revision"]
    rows_before = await _rows()

    response = await async_client.put(
        f"/api/accounts/{_ID}/lifecycle",
        json=_body(revision=stale["revision"], token=stale["concurrencyToken"], ends_on="2026-10-12", cancelled=True),
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "account_lifecycle_conflict"
    assert (await async_client.get(f"/api/accounts/{_ID}/lifecycle")).json() == current
    assert await _rows() == rows_before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    [None, "", "not-a-token", "A" * 64, "0" * 63],
    ids=["missing", "empty", "garbage", "upper-case", "short"],
)
async def test_the_token_is_required_and_well_formed(async_client, token):
    await _insert(_account())
    body = _body(revision=0, token=token, ends_on="2026-10-12")

    response = await async_client.put(f"/api/accounts/{_ID}/lifecycle", json=body)

    assert response.status_code == 422, response.text
    assert await _rows() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("orphan_revision", [0, 1], ids=["first-write", "existing-orphan"])
async def test_a_write_keyed_to_a_replaced_row_is_refused_inside_the_write(db_setup, orphan_revision):
    old_installation = str(uuid.uuid4())
    await _insert(_account(installation_id=old_installation))
    old_key = LifecycleKey(_ID, account_incarnation(old_installation))
    if orphan_revision:
        async with SessionLocal() as session:
            saved = await AccountLifecycleRepository(session).save(
                old_key, LifecycleColumns("2026-10-31", None, None, None, None, None, None), expected_revision=0
            )
        assert saved
    await _recycle()
    rows_before = await _rows()

    async with SessionLocal() as session:
        written = await AccountLifecycleRepository(session).save(
            old_key,
            LifecycleColumns("2026-10-12", None, None, None, None, None, "cancelled"),
            expected_revision=orphan_revision,
        )

    assert written is False
    assert await _rows() == rows_before


@pytest.mark.asyncio
async def test_a_write_for_an_account_pending_deletion_is_refused_inside_the_write(db_setup):
    installation_id = str(uuid.uuid4())
    await _insert(_account(installation_id=installation_id))
    async with SessionLocal() as session:
        await session.execute(update(Account).where(Account.id == _ID).values(delete_requested_at=utcnow()))
        await session.commit()

    async with SessionLocal() as session:
        written = await AccountLifecycleRepository(session).save(
            LifecycleKey(_ID, account_incarnation(installation_id)),
            LifecycleColumns("2026-10-12", None, None, None, None, None, None),
            expected_revision=0,
        )

    assert written is False
    assert await _rows() == []


@pytest.mark.asyncio
async def test_the_row_replaced_after_the_token_check_is_still_refused(async_client, monkeypatch):
    await _insert(_account())
    read = (await async_client.get(f"/api/accounts/{_ID}/lifecycle")).json()
    original_save = AccountLifecycleRepository.save

    async def _recycle_then_save(self, *args, **kwargs):
        await _recycle()
        return await original_save(self, *args, **kwargs)

    monkeypatch.setattr(AccountLifecycleRepository, "save", _recycle_then_save)

    response = await async_client.put(
        f"/api/accounts/{_ID}/lifecycle",
        json=_body(revision=read["revision"], token=read["concurrencyToken"], ends_on="2026-10-12"),
    )

    assert response.status_code == 409, response.text
    assert await _rows() == []
