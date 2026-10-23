from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update
from sqlalchemy.orm.attributes import set_committed_value

from app.core.auth.refresh import RefreshError
from app.core.clients.rate_limit_reset_credits import ResetCreditFetchError
from app.core.clients.usage import ConsumeRateLimitResetCreditResponse, UsageFetchError
from app.core.usage.models import UsagePayload
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.db.session import SessionLocal
from app.modules.accounts import service as accounts_service
from app.modules.accounts.auth_manager import AuthManager
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.rate_limit_reset_credits import api as reset_credits_api
from app.modules.rate_limit_reset_credits.redeem_coordination import get_pinned_redeem_credit_id
from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store
from app.modules.usage import updater as updater_module
from tests.integration.test_account_routing_hold import _expected, _repos, _seed
from tests.integration.test_rate_limit_reset_credits_api import (
    _credit,
    _import_test_account,
    _snapshot,
    _upstream_response,
)

pytestmark = pytest.mark.integration

_OLD_RESET = int(datetime.now(UTC).timestamp()) - 3600
_NEW_RESET = _OLD_RESET + 604800


def _forbid_side_effects(monkeypatch) -> None:
    monkeypatch.setattr(
        reset_credits_api, "consume_reset_credit", AsyncMock(side_effect=AssertionError("fixture forbids consume"))
    )
    monkeypatch.setattr(reset_credits_api, "_resolve_reset_credit_route", AsyncMock(return_value=None))
    monkeypatch.setattr(reset_credits_api, "_build_refresh_usage_callback", lambda context: None)
    monkeypatch.setattr(accounts_service, "propagate_account_routing_change", AsyncMock(return_value=True))
    monkeypatch.setattr(
        accounts_service,
        "consume_rate_limit_reset_credit",
        AsyncMock(side_effect=AssertionError("fixture forbids usage reset consume")),
    )
    monkeypatch.setattr(
        updater_module, "fetch_usage", AsyncMock(side_effect=AssertionError("fixture forbids usage fetch"))
    )
    monkeypatch.setattr(updater_module, "_resolve_upstream_route_for_account", AsyncMock(return_value=None))
    monkeypatch.setattr(
        AuthManager, "_refresh_tokens", AsyncMock(side_effect=AssertionError("fixture forbids credential exchange"))
    )


async def _set_account(account_id: str, **values: object) -> None:
    async with SessionLocal() as session:
        await session.execute(update(Account).where(Account.id == account_id).values(**values))
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("returned", "expected_reset_at", "proven"),
    [
        pytest.param("refill", None, True, id="refill-unconstrained"),
        pytest.param("refill", _NEW_RESET, True, id="refill-expects-new-reset"),
        pytest.param("refill", _OLD_RESET, False, id="refill-expects-old-reset"),
        pytest.param("primary_only", None, False, id="window-omitted"),
        pytest.param("failed", None, False, id="fetch-failed"),
    ],
)
@pytest.mark.parametrize("held", [False, True])
async def test_held_paused_usage_refresh_returns_the_persisted_window_reading(
    async_client, monkeypatch, held, returned, expected_reset_at, proven
):
    _forbid_side_effects(monkeypatch)
    account_id = await _seed(AccountStatus.PAUSED)
    await _set_account(account_id, routing_policy="preserve", routing_held=held)
    async with SessionLocal() as session:
        session.add(
            UsageHistory(
                account_id=account_id,
                window="secondary",
                used_percent=100,
                reset_at=_OLD_RESET,
                window_minutes=10080,
                recorded_at=utcnow() - timedelta(days=8),
            )
        )
        await session.commit()

    async def fetch(**kwargs: object) -> UsagePayload:
        assert (await LoadBalancer(_repos).select_account(account_ids=[account_id])).account is None
        if returned == "failed":
            raise UsageFetchError(500, "fixture failure")
        window = (
            {"secondary_window": {"used_percent": 0, "limit_window_seconds": 604800, "reset_at": _NEW_RESET}}
            if returned == "refill"
            else {"primary_window": {"used_percent": 5, "limit_window_seconds": 18000, "reset_at": _NEW_RESET}}
        )
        return UsagePayload.model_validate({"plan_type": "plus", "rate_limit": window})

    fetched = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(updater_module, "fetch_usage", fetched)
    payload: dict[str, object] = {
        "window": "secondary",
        "expected": _expected(AccountStatus.PAUSED, policy="preserve", held=held),
    }
    if expected_reset_at is not None:
        payload["expectedResetAt"] = expected_reset_at
    response = await async_client.post(f"/api/accounts/{account_id}/usage-refresh", json=payload)

    assert response.status_code == 200, response.text
    body = response.json()
    fetched.assert_awaited_once()
    assert body["fetchSucceeded"] is (returned != "failed")
    assert body["refreshed"] is proven
    assert (body["status"], body["routingPolicy"], body["routingHeld"]) == ("paused", "preserve", held)
    if proven:
        assert (body["resetAt"], body.get("usedPercent")) == (_NEW_RESET, 0.0)
        async with SessionLocal() as session:
            row = await session.get(UsageHistory, body["usageId"])
            assert row is not None
            assert (row.window, row.reset_at, row.used_percent) == ("secondary", _NEW_RESET, 0.0)
            assert row.recorded_at.isoformat() + "Z" == body["recordedAt"]
    else:
        assert (body["usageId"], body["recordedAt"], body["resetAt"], body.get("usedPercent")) == (None,) * 4
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        assert (account.status, account.routing_policy, account.routing_held) == (
            AccountStatus.PAUSED,
            "preserve",
            held,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["none", "policy", "deleted", "identity-committed", "identity-same-object"])
async def test_held_usage_refresh_refuses_a_reading_polled_for_a_replaced_identity(async_client, monkeypatch, change):
    _forbid_side_effects(monkeypatch)
    account_id = await _seed(AccountStatus.PAUSED)
    await _set_account(account_id, routing_policy="preserve", routing_held=True)
    polled: list[Account] = []
    force_refresh_result = updater_module.UsageUpdater.force_refresh_result

    async def keep_polled_account(self, account: Account, *, access_token_override: str | None = None):
        polled.append(account)
        return await force_refresh_result(self, account, access_token_override=access_token_override)

    async def fetch(**kwargs: object) -> UsagePayload:
        assert kwargs["account_id"] == "fixture-workspace"
        if change == "policy":
            await _set_account(account_id, routing_policy="normal")
        elif change == "deleted":
            await _set_account(account_id, delete_requested_at=utcnow())
        elif change != "none":
            await _set_account(account_id, chatgpt_account_id="replacement-workspace")
            if change == "identity-same-object":
                set_committed_value(polled[0], "chatgpt_account_id", "replacement-workspace")
        window = {"used_percent": 0, "limit_window_seconds": 604800, "reset_at": _NEW_RESET}
        return UsagePayload.model_validate({"plan_type": "plus", "rate_limit": {"secondary_window": window}})

    monkeypatch.setattr(updater_module.UsageUpdater, "force_refresh_result", keep_polled_account)
    fetched = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(updater_module, "fetch_usage", fetched)
    response = await async_client.post(
        f"/api/accounts/{account_id}/usage-refresh",
        json={"window": "secondary", "expected": _expected(AccountStatus.PAUSED, policy="preserve", held=True)},
    )

    fetched.assert_awaited_once()
    if change == "none":
        assert response.status_code == 200, response.text
        assert (response.json()["refreshed"], response.json()["usedPercent"]) == (True, 0.0)
    elif change == "deleted":
        assert response.status_code == 404, response.text
    else:
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "account_state_transition_invalid"
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        assert account.chatgpt_account_id == (
            "replacement-workspace" if change.startswith("identity") else "fixture-workspace"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream", ["credit", "empty", "failure"])
@pytest.mark.parametrize("cache_state", ["stale", "absent"])
async def test_fresh_reset_read_distinguishes_fresh_empty_failure_and_stale_cache(
    async_client, monkeypatch, cache_state, upstream
):
    _forbid_side_effects(monkeypatch)
    stale = _credit("cached-stale-credit", expires_at="2027-07-01T00:00:00Z")
    fresh = _credit("fresh-upstream-credit", expires_at="2027-07-12T00:00:00Z")
    fetched = (
        AsyncMock(side_effect=ResetCreditFetchError(503, "fixture upstream failure"))
        if upstream == "failure"
        else AsyncMock(return_value=_upstream_response([fresh] if upstream == "credit" else []))
    )
    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", fetched)
    account_id = await _import_test_account(
        async_client, email="fresh-read@example.invalid", account_id="fresh-read-workspace"
    )
    store = get_rate_limit_reset_credits_store()
    await store.invalidate(account_id)
    if cache_state == "stale":
        await store.set(account_id, _snapshot([stale]))
    cached = store.get(account_id)
    try:
        before = datetime.now(UTC)
        response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/refresh")
        after = datetime.now(UTC)

        fetched.assert_awaited_once()
        assert fetched.await_args is not None and fetched.await_args.args[1] == "fresh-read-workspace"
        if upstream == "failure":
            assert response.status_code == 503, response.text
            assert "credits" not in response.json()
        else:
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["accountId"] == account_id
            assert before <= datetime.fromisoformat(body["fetchedAt"].replace("Z", "+00:00")) <= after
            assert body["availableCount"] == (1 if upstream == "credit" else 0)
            assert [credit["id"] for credit in body["credits"]] == ([fresh.id] if upstream == "credit" else [])
        assert store.get(account_id) is cached
    finally:
        await store.invalidate(account_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED])
async def test_fresh_reset_read_inspects_held_account_while_serving_stays_blocked(async_client, monkeypatch, status):
    _forbid_side_effects(monkeypatch)
    credit = _credit("held-account-credit", expires_at="2027-07-12T00:00:00Z")
    account_id = await _import_test_account(
        async_client, email="held-read@example.invalid", account_id="held-read-workspace"
    )
    await _set_account(account_id, status=status, routing_policy="preserve", routing_held=True)

    async def fetch(*args: object, **kwargs: object):
        assert (await LoadBalancer(_repos).select_account(account_ids=[account_id])).account is None
        return _upstream_response([credit])

    fetched = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", fetched)
    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/refresh")

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["credits"]] == [credit.id]
    fetched.assert_awaited_once()
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        assert (account.status, account.routing_policy, account.routing_held) == (status, "preserve", True)
    cached = await async_client.get(f"/api/accounts/{account_id}/rate-limit-reset-credits")
    assert cached.status_code == 200 and cached.json() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["identity", "deleted"])
async def test_fresh_reset_read_refuses_an_observation_that_outlived_its_account(async_client, monkeypatch, change):
    _forbid_side_effects(monkeypatch)
    account_id = await _import_test_account(
        async_client, email="identity-read@example.invalid", account_id="identity-read-workspace"
    )

    async def fetch(access_token: str, chatgpt_account_id: str | None, **kwargs: object):
        assert chatgpt_account_id == "identity-read-workspace"
        await _set_account(
            account_id,
            **(
                {"chatgpt_account_id": "replacement-workspace"}
                if change == "identity"
                else {"delete_requested_at": utcnow()}
            ),
        )
        return _upstream_response([_credit("previous-identity-credit", expires_at="2027-07-12T00:00:00Z")])

    fetched = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", fetched)
    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/refresh")

    fetched.assert_awaited_once()
    assert response.status_code == (409 if change == "identity" else 404), response.text
    assert response.json()["error"]["code"] == (
        "account_reset_credit_identity_changed" if change == "identity" else "account_not_found"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["deactivated", "credentials"])
async def test_fresh_reset_read_reports_unavailable_account_boundaries(async_client, monkeypatch, boundary):
    _forbid_side_effects(monkeypatch)
    fetched = AsyncMock(side_effect=AssertionError("an unavailable account must not fetch"))
    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", fetched)
    account_id = await _import_test_account(
        async_client, email="boundary-read@example.invalid", account_id="boundary-read-workspace"
    )
    if boundary == "deactivated":
        await _set_account(account_id, status=AccountStatus.DEACTIVATED)
    else:
        monkeypatch.setattr(
            AuthManager,
            "ensure_fresh",
            AsyncMock(side_effect=RefreshError("refresh_token_expired", "fixture credential boundary", False)),
        )
    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/refresh")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == (
        "account_not_reset_credit_applicable" if boundary == "deactivated" else "account_reset_credit_refresh_failed"
    )
    fetched.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("expectation", ["id", "expiry"])
async def test_exact_consume_without_cache_still_refuses_a_missing_target(async_client, monkeypatch, expectation):
    _forbid_side_effects(monkeypatch)
    approved = _credit("approved-credit", expires_at="2027-07-01T00:00:00Z")
    assert approved.expires_at is not None
    fetched = AsyncMock(return_value=_upstream_response([_credit("other-credit", expires_at="2027-07-12T00:00:00Z")]))
    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", fetched)
    account_id = await _import_test_account(
        async_client, email="missing-target@example.invalid", account_id="missing-target-workspace"
    )
    store = get_rate_limit_reset_credits_store()
    await store.invalidate(account_id)
    payload = {"redeemRequestId": "approved-request"}
    if expectation == "id":
        payload["expectedCreditId"] = approved.id
    else:
        payload["expectedCreditExpiresAt"] = approved.expires_at.isoformat()
    try:
        response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume", json=payload)

        assert fetched.await_count == 1, "cached absence suppressed authoritative pre-consume discovery"
        assert response.status_code == 409, response.text
        assert await get_pinned_redeem_credit_id(account_id, "approved-request") is None
    finally:
        await store.invalidate(account_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("expectation", ["expectedCreditId", "expectedCreditExpiresAt"])
async def test_untargeted_usage_reset_consume_refuses_caller_expectations(async_client, monkeypatch, expectation):
    _forbid_side_effects(monkeypatch)
    consumed = AsyncMock(
        return_value=ConsumeRateLimitResetCreditResponse.model_validate({"code": "reset", "windows_reset": 1})
    )
    monkeypatch.setattr(accounts_service, "consume_rate_limit_reset_credit", consumed)
    account_id = await _import_test_account(
        async_client, email="untargeted@example.invalid", account_id="untargeted-workspace"
    )
    response = await async_client.post(
        f"/api/accounts/{account_id}/usage-reset-credits/consume",
        json={
            "redeemRequestId": "approved-request",
            expectation: "approved-credit" if expectation == "expectedCreditId" else "2027-07-12T00:00:00Z",
        },
    )

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "reset_credit_expectation_unsupported"
    consumed.assert_not_awaited()
