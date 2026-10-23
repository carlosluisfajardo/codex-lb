from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update

from app.core.clients.rate_limit_reset_credits import ConsumeResetCreditResponse
from app.db.models import Account
from app.db.session import SessionLocal
from app.modules.accounts import service as accounts_service
from app.modules.accounts.auth_manager import AuthManager
from app.modules.rate_limit_reset_credits import api as reset_credits_api
from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store
from tests.integration.test_rate_limit_reset_credits_api import (
    _credit,
    _import_test_account,
    _snapshot,
    _upstream_response,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("cache_state", ["available", "empty", "absent"])
@pytest.mark.parametrize("held", [False, True])
async def test_exact_consume_discovers_fresh_credit_without_cached_availability(
    async_client, monkeypatch, cache_state, held
):
    credit = _credit("newly-granted-credit", expires_at="2027-07-12T00:00:00Z")
    assert credit.expires_at is not None
    fetched = AsyncMock(return_value=_upstream_response([credit]))
    consumed = AsyncMock(
        return_value=ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "credit": {"id": credit.id, "status": "redeemed", "redeemed_at": "2026-10-22T00:00:00Z"},
                "windows_reset": 1,
            }
        )
    )
    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", fetched)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", consumed)
    monkeypatch.setattr(reset_credits_api, "_resolve_reset_credit_route", AsyncMock(return_value=None))
    monkeypatch.setattr(reset_credits_api, "_build_refresh_usage_callback", lambda context: None)
    monkeypatch.setattr(accounts_service, "propagate_account_routing_change", AsyncMock(return_value=True))
    monkeypatch.setattr(
        AuthManager, "_refresh_tokens", AsyncMock(side_effect=AssertionError("fixture must not exchange credentials"))
    )
    account_id = await _import_test_account(
        async_client, email="discovery-cache@example.invalid", account_id="discovery-workspace"
    )
    if held:
        async with SessionLocal() as session:
            await session.execute(update(Account).where(Account.id == account_id).values(routing_held=True))
            await session.commit()
    store = get_rate_limit_reset_credits_store()
    await store.invalidate(account_id)
    if cache_state != "absent":
        await store.set(account_id, _snapshot([credit] if cache_state == "available" else []))
    try:
        response = await async_client.post(
            f"/api/accounts/{account_id}/rate-limit-reset-credits/consume",
            json={
                "redeemRequestId": "fresh-discovery-request",
                "expectedCreditId": credit.id,
                "expectedCreditExpiresAt": credit.expires_at.isoformat(),
            },
        )
        assert fetched.await_count == 1, "cached absence suppressed authoritative pre-consume discovery"
        assert response.status_code == 200, response.text
        consumed.assert_awaited_once()
        assert consumed.await_args is not None
        assert consumed.await_args.args[1:3] == ("discovery-workspace", credit.id)
        async with SessionLocal() as session:
            account = await session.get(Account, account_id)
            assert account is not None
            assert account.routing_held is held
    finally:
        await store.invalidate(account_id)
