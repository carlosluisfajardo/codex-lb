from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clients.rate_limit_reset_credits import ConsumeResetCreditResponse
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ResetCreditRedeemClaim
from app.db.session import SessionLocal
from app.modules.accounts import service as accounts_service
from app.modules.accounts.auth_manager import AuthManager
from app.modules.rate_limit_reset_credits import api as reset_credits_api
from app.modules.rate_limit_reset_credits import redeem_coordination as coordination
from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store
from tests.integration.test_rate_limit_reset_credits_api import (
    _credit,
    _import_test_account,
    _snapshot,
    _upstream_response,
)

pytestmark = pytest.mark.integration

_WAITS = ("serialization", "auth", "route", "fetch", "claim")
_CHANGES = ("eligible", "held", "paused", "reauth_required", "deactivated", "pending_delete", "identity")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("wait_at", "change"),
    [(wait_at, change) for wait_at in _WAITS for change in _CHANGES] + [("claim", "missing")],
)
async def test_dashboard_redemption_rechecks_account_after_waits(async_client, monkeypatch, wait_at, change):
    account_id = await _import_test_account(
        async_client, email="eligibility-race@example.invalid", account_id="original-workspace"
    )
    credit = _credit("approved-credit", expires_at="2027-07-12T00:00:00Z")
    assert credit.expires_at is not None
    store = get_rate_limit_reset_credits_store()
    await store.set(account_id, _snapshot([credit]))
    entered, release = asyncio.Event(), asyncio.Event()
    stages = []
    original_try_acquire = coordination.try_acquire_redeem_claim
    original_ensure_fresh = AuthManager.ensure_fresh
    original_renew = reset_credits_api.renew_redeem_claim

    async def barrier(stage):
        if stage == wait_at:
            entered.set()
            await release.wait()

    async def try_acquire(target, holder, **kwargs):
        acquired = await original_try_acquire(target, holder, **kwargs)
        if not acquired:
            await barrier("serialization")
        return acquired

    async def ensure_fresh(self, account, *, force=False):
        stages.append("auth")
        await barrier("auth")
        return await original_ensure_fresh(self, account, force=force)

    async def resolve_route(account):
        stages.append("route")
        await barrier("route")
        return None

    async def fetch(*args, **kwargs):
        stages.append("fetch")
        await barrier("fetch")
        return _upstream_response([credit])

    async def renew(target, holder, **kwargs):
        owned = await original_renew(target, holder, **kwargs)
        # Suspend after the real admission renewal, so a final account read
        # before (rather than after) that await cannot satisfy this case.
        await barrier("claim")
        return owned

    consumed = AsyncMock(
        return_value=ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "credit": {"id": credit.id, "status": "redeemed", "redeemed_at": "2026-10-22T00:00:00Z"},
                "windows_reset": 1,
            }
        )
    )
    propagation = AsyncMock(return_value=True)
    monkeypatch.setattr(coordination, "try_acquire_redeem_claim", try_acquire)
    monkeypatch.setattr(AuthManager, "ensure_fresh", ensure_fresh)
    monkeypatch.setattr(
        AuthManager, "_refresh_tokens", AsyncMock(side_effect=AssertionError("fixture must not refresh upstream"))
    )
    monkeypatch.setattr(reset_credits_api, "_resolve_reset_credit_route", resolve_route)
    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", fetch)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", consumed)
    monkeypatch.setattr(reset_credits_api, "renew_redeem_claim", renew)
    monkeypatch.setattr(reset_credits_api, "_build_refresh_usage_callback", lambda context: None)
    monkeypatch.setattr(accounts_service, "propagate_account_routing_change", propagation)

    if wait_at == "serialization":
        assert await original_try_acquire(account_id, "fixture-predecessor")
    pending = asyncio.create_task(
        async_client.post(
            f"/api/accounts/{account_id}/rate-limit-reset-credits/consume",
            json={
                "redeemRequestId": "retained-request",
                "expectedCreditId": credit.id,
                "expectedCreditExpiresAt": credit.expires_at.isoformat(),
            },
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 3)
        if change == "paused":
            response = await async_client.post(f"/api/accounts/{account_id}/pause")
            assert response.status_code == 200, response.text
            propagation.assert_awaited()
        elif change == "held":
            response = await async_client.put(
                f"/api/accounts/{account_id}/routing-hold",
                json={"held": True, "expected": {"status": "active", "routingPolicy": "normal", "routingHeld": False}},
            )
            assert response.status_code == 200, response.text
            propagation.assert_awaited()
        elif change != "eligible":
            async with SessionLocal() as session:
                if change == "missing":
                    await session.execute(delete(Account).where(Account.id == account_id))
                else:
                    values = (
                        {"chatgpt_account_id": "replacement-workspace"}
                        if change == "identity"
                        else {"delete_requested_at": utcnow(), "status": AccountStatus.DEACTIVATED}
                        if change == "pending_delete"
                        else {"status": AccountStatus(change)}
                    )
                    await session.execute(update(Account).where(Account.id == account_id).values(**values))
                await session.commit()
        if wait_at == "serialization":
            await coordination.release_redeem_claim(account_id, "fixture-predecessor")
        release.set()
        response = await asyncio.wait_for(pending, 3)
        if change in {"eligible", "held"}:
            assert response.status_code == 200, response.text
            consumed.assert_awaited_once()
            assert consumed.await_args is not None
            assert consumed.await_args.args[1:3] == ("original-workspace", credit.id)
            assert consumed.await_args.kwargs["redeem_request_id"] == "retained-request"
        else:
            assert consumed.await_count == 0, "reset consume dispatched after account became ineligible or changed"
            assert response.status_code == (404 if change in {"pending_delete", "missing"} else 409), response.text
            if wait_at == "serialization":
                assert stages == [], "ineligible serialization waiter entered credential/upstream work"
        async with SessionLocal() as session:
            current = await session.get(Account, account_id)
            assert await session.get(ResetCreditRedeemClaim, account_id) is None
            if change == "missing":
                assert current is None
            else:
                assert current is not None
                if change in {"paused", "reauth_required", "deactivated"}:
                    assert current.status == AccountStatus(change)
                if change == "identity":
                    assert current.chatgpt_account_id == "replacement-workspace"
                if change == "pending_delete":
                    assert current.delete_requested_at is not None
                if change == "held":
                    assert current.routing_held
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await coordination.release_redeem_claim(account_id, "fixture-predecessor")
        await store.invalidate(account_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("held", [False, True])
@pytest.mark.parametrize("claim_change", ["owned", "expired", "successor"])
async def test_final_account_read_retains_claim_admission(async_client, monkeypatch, held, claim_change):
    account_id = await _import_test_account(
        async_client, email="claim-read-race@example.invalid", account_id="claim-read-workspace"
    )
    if held:
        async with SessionLocal() as session:
            await session.execute(update(Account).where(Account.id == account_id).values(routing_held=True))
            await session.commit()
    credit = _credit("retained-credit", expires_at="2027-07-12T00:00:00Z")
    store = get_rate_limit_reset_credits_store()
    await store.set(account_id, _snapshot([credit]))
    entered, release = asyncio.Event(), asyncio.Event()
    account_reads = 0
    original_execute = AsyncSession.execute

    async def execute(self, statement, *args, **kwargs):
        nonlocal account_reads
        if (
            isinstance(statement, Select)
            and statement.get_execution_options().get("populate_existing")
            and statement.column_descriptions[0].get("entity") is Account
        ):
            account_reads += 1
            if account_reads == 2:
                entered.set()
                await release.wait()
        return await original_execute(self, statement, *args, **kwargs)

    consumed = AsyncMock(
        return_value=ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "credit": {"id": credit.id, "status": "redeemed", "redeemed_at": "2026-10-22T00:00:00Z"},
                "windows_reset": 1,
            }
        )
    )
    monkeypatch.setattr(AsyncSession, "execute", execute)
    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", AsyncMock(return_value=_upstream_response([credit])))
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", consumed)
    monkeypatch.setattr(reset_credits_api, "_resolve_reset_credit_route", AsyncMock(return_value=None))
    monkeypatch.setattr(reset_credits_api, "_build_refresh_usage_callback", lambda context: None)
    monkeypatch.setattr(
        AuthManager, "_refresh_tokens", AsyncMock(side_effect=AssertionError("fixture must not refresh upstream"))
    )
    pending = asyncio.create_task(
        async_client.post(
            f"/api/accounts/{account_id}/rate-limit-reset-credits/consume",
            json={"redeemRequestId": "read-race-request", "expectedCreditId": credit.id},
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 3)
        if claim_change != "owned":
            async with SessionLocal() as session:
                await session.execute(
                    update(ResetCreditRedeemClaim)
                    .where(ResetCreditRedeemClaim.account_id == account_id)
                    .values(expires_at=datetime.now(UTC))
                )
                await session.commit()
            if claim_change == "successor":
                assert await coordination.try_acquire_redeem_claim(account_id, "read-race-successor")
        release.set()
        response = await asyncio.wait_for(pending, 3)
        if claim_change == "owned":
            assert response.status_code == 200, response.text
            consumed.assert_awaited_once()
        else:
            assert consumed.await_count == 0, "final account read admitted consume after claim ownership was lost"
            assert response.status_code == 409, response.text
            assert response.json()["error"]["code"] == "reset_credit_redeem_claim_lost"
        assert await coordination.get_pinned_redeem_credit_id(account_id, "read-race-request") == credit.id
        async with SessionLocal() as session:
            remaining = await session.get(ResetCreditRedeemClaim, account_id)
            assert (remaining.holder_id if remaining else None) == (
                "read-race-successor" if claim_change == "successor" else None
            )
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await coordination.release_redeem_claim(account_id, "read-race-successor")
        await store.invalidate(account_id)
