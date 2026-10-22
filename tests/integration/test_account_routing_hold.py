from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import update

from app.core.clients.usage import UsageFetchError
from app.core.crypto import TokenEncryptor
from app.core.usage.models import UsagePayload
from app.core.utils.time import naive_utc_to_epoch, utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy import account_cache
from app.modules.proxy._service.http_bridge.helpers import _http_bridge_session_account_active
from app.modules.proxy._service.support import _HTTPBridgeSession
from app.modules.proxy.account_cache import RoutingAvailabilityCache, clear_all_account_routing_unavailable
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage import updater as updater_module
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _repos() -> AsyncIterator[ProxyRepositories]:
    async with SessionLocal() as session:
        yield ProxyRepositories(
            accounts=AccountsRepository(session),
            usage=UsageRepository(session),
            sticky_sessions=StickySessionsRepository(session),
            api_keys=ApiKeysRepository(session),
            request_logs=RequestLogsRepository(session),
            additional_usage=AdditionalUsageRepository(session),
        )


async def _seed(status: AccountStatus) -> str:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="held-account",
            email="held@example.invalid",
            chatgpt_account_id="fixture-workspace",
            plan_type="plus",
            status=status,
            access_token_encrypted=encryptor.encrypt("fixture-access"),
            refresh_token_encrypted=encryptor.encrypt("fixture-refresh"),
            id_token_encrypted=encryptor.encrypt("fixture-id"),
            last_refresh=utcnow(),
        )
        session.add(account)
        if status in (AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED):
            now = utcnow()
            reset_at = int(naive_utc_to_epoch(now - timedelta(minutes=10)))
            account.reset_at = reset_at
            account.blocked_at = reset_at - 1
            await session.flush()
            for window, minutes in (("primary", 300), ("secondary", 10080)):
                session.add(
                    UsageHistory(
                        account_id=account.id,
                        window=window,
                        used_percent=10,
                        reset_at=int(naive_utc_to_epoch(now + timedelta(minutes=minutes))),
                        window_minutes=minutes,
                        recorded_at=now,
                    )
                )
        await session.commit()
        return account.id


def _expected(status: AccountStatus, *, policy: str = "normal", held: bool = False) -> dict[str, object]:
    return {"status": status.value, "routingPolicy": policy, "routingHeld": held}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [AccountStatus.ACTIVE, AccountStatus.REAUTH_REQUIRED, AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED],
)
async def test_routing_hold_blocks_every_routable_state_without_replacing_auth_state(async_client, status):
    account_id = await _seed(status)
    response = await async_client.put(
        f"/api/accounts/{account_id}/routing-hold", json={"held": True, "expected": _expected(status)}
    )
    assert response.status_code == 200, response.text
    assert response.json()["routingHeld"] is True
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None and account.status == status and account.routing_held is True
    # A cold replica has no process-local unavailable mark; selection must
    # also enforce the persisted hold directly from its account snapshot.
    clear_all_account_routing_unavailable()
    result = await LoadBalancer(_repos).select_account(account_ids=[account_id])
    assert result.account is None
    listed = await async_client.get("/api/accounts")
    assert next(row for row in listed.json()["accounts"] if row["accountId"] == account_id)["routingHeld"] is True
    released = await async_client.put(
        f"/api/accounts/{account_id}/routing-hold", json={"held": False, "expected": _expected(status, held=True)}
    )
    assert released.status_code == 200
    routable = await LoadBalancer(_repos).select_account(account_ids=[account_id])
    assert routable.account is not None and routable.account.id == account_id


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["routing-policy", "pause", "reactivate"])
async def test_caller_expected_routing_state_rejects_intervening_human_policy(async_client, action):
    status = AccountStatus.PAUSED if action == "reactivate" else AccountStatus.ACTIVE
    account_id = await _seed(status)
    observed = _expected(status)
    async with SessionLocal() as session:
        await session.execute(update(Account).where(Account.id == account_id).values(routing_policy="preserve"))
        await session.commit()
    payload = {"expected": observed}
    if action == "routing-policy":
        payload["routingPolicy"] = "burn_first"
    response = await (async_client.put if action == "routing-policy" else async_client.post)(
        f"/api/accounts/{account_id}/{action}", json=payload
    )
    assert response.status_code == 409, response.text
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None and account.status == status and account.routing_policy == "preserve"


@pytest.mark.asyncio
@pytest.mark.parametrize("returned_window", ["secondary", "primary", "empty", "failed", "different_reset"])
async def test_held_reauth_refresh_proves_only_the_requested_window(async_client, monkeypatch, returned_window):
    account_id = await _seed(AccountStatus.REAUTH_REQUIRED)
    held = await async_client.put(
        f"/api/accounts/{account_id}/routing-hold",
        json={"held": True, "expected": _expected(AccountStatus.REAUTH_REQUIRED)},
    )
    assert held.status_code == 200, held.text
    called = False

    async def fetch(**kwargs: object) -> UsagePayload:
        nonlocal called
        called = True
        result = await LoadBalancer(_repos).select_account(account_ids=[account_id])
        assert result.account is None
        if returned_window == "failed":
            raise UsageFetchError(500, "fixture failure")
        window = "primary_window" if returned_window == "primary" else "secondary_window"
        rate_limit = (
            {}
            if returned_window == "empty"
            else {
                window: {
                    "used_percent": 20,
                    "limit_window_seconds": 18000 if returned_window == "primary" else 604800,
                    "reset_at": 2100000001 if returned_window == "different_reset" else 2100000000,
                }
            }
        )
        return UsagePayload.model_validate({"plan_type": "plus", "rate_limit": rate_limit})

    monkeypatch.setattr(updater_module, "fetch_usage", fetch)
    response = await async_client.post(
        f"/api/accounts/{account_id}/usage-refresh",
        json={
            "window": "secondary",
            "expectedResetAt": 2100000000,
            "expected": _expected(AccountStatus.REAUTH_REQUIRED, held=True),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert called
    assert body["refreshed"] is (returned_window == "secondary")
    assert body["fetchSucceeded"] is (returned_window != "failed")
    assert body["routingHeld"] is True and body["status"] == "reauth_required"
    assert (body["recordedAt"] is not None) is (returned_window == "secondary")
    assert (body["usageId"] is not None) is (returned_window == "secondary")
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None and account.routing_held and account.status == AccountStatus.REAUTH_REQUIRED


@pytest.mark.asyncio
async def test_release_hold_refuses_a_human_policy_change(async_client):
    account_id = await _seed(AccountStatus.ACTIVE)
    held = await async_client.put(
        f"/api/accounts/{account_id}/routing-hold", json={"held": True, "expected": _expected(AccountStatus.ACTIVE)}
    )
    assert held.status_code == 200
    async with SessionLocal() as session:
        await session.execute(update(Account).where(Account.id == account_id).values(routing_policy="preserve"))
        await session.commit()
    response = await async_client.put(
        f"/api/accounts/{account_id}/routing-hold",
        json={"held": False, "expected": _expected(AccountStatus.ACTIVE, held=True)},
    )
    assert response.status_code == 409
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None and account.routing_held and account.routing_policy == "preserve"


@pytest.mark.asyncio
async def test_hold_survives_replica_cache_refresh_and_reauthentication_clear(async_client):
    account_id = await _seed(AccountStatus.REAUTH_REQUIRED)
    response = await async_client.put(
        f"/api/accounts/{account_id}/routing-hold",
        json={"held": True, "expected": _expected(AccountStatus.REAUTH_REQUIRED)},
    )
    assert response.status_code == 200
    replica = RoutingAvailabilityCache()
    await replica.refresh_from_db()
    assert replica.is_unavailable(account_id)
    replica.clear_unavailable(account_id)
    assert replica.is_unavailable(account_id)
    released = await async_client.put(
        f"/api/accounts/{account_id}/routing-hold",
        json={"held": False, "expected": _expected(AccountStatus.REAUTH_REQUIRED, held=True)},
    )
    assert released.status_code == 200
    await replica.refresh_from_db()
    assert not replica.is_unavailable(account_id)


@pytest.mark.asyncio
async def test_completed_hold_survives_an_older_cache_refresh_and_reauthentication_clear(async_client, monkeypatch):
    account_id = await _seed(AccountStatus.REAUTH_REQUIRED)
    cache = RoutingAvailabilityCache()
    monkeypatch.setattr(account_cache, "_routing_availability_cache", cache)
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
    bridge = Mock(spec=_HTTPBridgeSession, account=account, access_token_expires_at=None)
    await cache.refresh_from_db()
    assert _http_bridge_session_account_active(bridge)
    older_read, release_older = asyncio.Event(), asyncio.Event()
    original_close = account_cache.close_session
    first_close = True

    async def delayed_close(session):
        nonlocal first_close
        await original_close(session)
        if first_close:
            first_close = False
            older_read.set()
            await release_older.wait()

    monkeypatch.setattr(account_cache, "close_session", delayed_close)
    older = asyncio.create_task(cache.refresh_from_db())
    try:
        await asyncio.wait_for(older_read.wait(), 2)
        held = await asyncio.wait_for(
            async_client.put(
                f"/api/accounts/{account_id}/routing-hold",
                json={"held": True, "expected": _expected(AccountStatus.REAUTH_REQUIRED)},
            ),
            2,
        )
        assert held.status_code == 200, held.text
        assert not older.done() and not release_older.is_set()
        assert cache.is_unavailable(account_id) and not _http_bridge_session_account_active(bridge)
        release_older.set()
        await asyncio.wait_for(older, 2)
        cache.clear_unavailable(account_id)
        async with SessionLocal() as session:
            account = await session.get(Account, account_id)
            assert account is not None and account.routing_held
        assert (cache.is_unavailable(account_id), _http_bridge_session_account_active(bridge)) == (True, False)

        released = await async_client.put(
            f"/api/accounts/{account_id}/routing-hold",
            json={"held": False, "expected": _expected(AccountStatus.REAUTH_REQUIRED, held=True)},
        )
        assert released.status_code == 200, released.text
        assert not cache.is_unavailable(account_id) and _http_bridge_session_account_active(bridge)
    finally:
        release_older.set()
        if not older.done():
            older.cancel()
        await asyncio.gather(older, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("newer_refresh_fails", [False, True])
async def test_completed_hold_publishes_while_a_newer_refresh_is_pending(
    async_client, monkeypatch, newer_refresh_fails
):
    account_id = await _seed(AccountStatus.REAUTH_REQUIRED)
    cache = RoutingAvailabilityCache()
    monkeypatch.setattr(account_cache, "_routing_availability_cache", cache)
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
    bridge = Mock(spec=_HTTPBridgeSession, account=account, access_token_expires_at=None)
    await cache.refresh_from_db()
    assert _http_bridge_session_account_active(bridge)
    held_read, newer_read = asyncio.Event(), asyncio.Event()
    release_held, release_newer = asyncio.Event(), asyncio.Event()
    original_close = account_cache.close_session
    close_count = 0

    async def delayed_close(session):
        nonlocal close_count
        await original_close(session)
        close_count += 1
        if close_count == 1:
            held_read.set()
            await release_held.wait()
        elif close_count == 2:
            newer_read.set()
            await release_newer.wait()
            if newer_refresh_fails:
                raise RuntimeError("injected newer refresh failure")

    monkeypatch.setattr(account_cache, "close_session", delayed_close)
    held_task = asyncio.create_task(
        async_client.put(
            f"/api/accounts/{account_id}/routing-hold",
            json={"held": True, "expected": _expected(AccountStatus.REAUTH_REQUIRED)},
        )
    )
    newer = None
    try:
        await asyncio.wait_for(held_read.wait(), 2)
        newer = asyncio.create_task(cache.refresh_from_db())
        await asyncio.wait_for(newer_read.wait(), 2)
        release_held.set()
        held = await asyncio.wait_for(held_task, 2)
        assert held.status_code == 200, held.text
        assert not newer.done() and not release_newer.is_set()
        cache.clear_unavailable(account_id)
        async with SessionLocal() as session:
            account = await session.get(Account, account_id)
            assert account is not None and account.routing_held
        assert (cache.is_unavailable(account_id), _http_bridge_session_account_active(bridge)) == (True, False)

        release_newer.set()
        if newer_refresh_fails:
            with pytest.raises(RuntimeError, match="injected newer refresh failure"):
                await asyncio.wait_for(newer, 2)
        else:
            await asyncio.wait_for(newer, 2)
        cache.clear_unavailable(account_id)
        assert (cache.is_unavailable(account_id), _http_bridge_session_account_active(bridge)) == (True, False)
        released = await async_client.put(
            f"/api/accounts/{account_id}/routing-hold",
            json={"held": False, "expected": _expected(AccountStatus.REAUTH_REQUIRED, held=True)},
        )
        assert released.status_code == 200, released.text
        assert not cache.is_unavailable(account_id) and _http_bridge_session_account_active(bridge)
    finally:
        release_held.set()
        release_newer.set()
        tasks = [held_task] + ([newer] if newer is not None else [])
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_reset_prevents_inflight_routing_snapshot_publication(async_client, monkeypatch):
    await _seed(AccountStatus.ACTIVE)
    cache = RoutingAvailabilityCache()
    await cache.refresh_from_db()
    read, release = asyncio.Event(), asyncio.Event()
    original_close = account_cache.close_session

    async def delayed_close(session):
        await original_close(session)
        read.set()
        await release.wait()

    monkeypatch.setattr(account_cache, "close_session", delayed_close)
    pending = asyncio.create_task(cache.refresh_from_db())
    try:
        await asyncio.wait_for(read.wait(), 2)
        cache.reset()
        release.set()
        await asyncio.wait_for(pending, 2)
        assert not cache.seeded
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["routing-policy", "pause", "reactivate", "routing-hold"])
async def test_routing_write_compares_expected_policy_in_the_database(async_client, monkeypatch, action):
    status = AccountStatus.PAUSED if action == "reactivate" else AccountStatus.ACTIVE
    account_id = await _seed(status)
    method = {"routing-policy": "update_routing_policy", "routing-hold": "update_routing_hold_if_current"}.get(
        action, "update_status_if_current"
    )
    original = getattr(AccountsRepository, method)

    async def concurrent_human_change(self, *args, **kwargs):
        async with SessionLocal() as session:
            await session.execute(update(Account).where(Account.id == account_id).values(routing_policy="preserve"))
            await session.commit()
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(AccountsRepository, method, concurrent_human_change)
    payload = {"expected": _expected(status)}
    if action == "routing-policy":
        payload["routingPolicy"] = "burn_first"
    if action == "routing-hold":
        payload["held"] = True
    response = await (async_client.put if action in ("routing-policy", "routing-hold") else async_client.post)(
        f"/api/accounts/{account_id}/{action}", json=payload
    )
    assert response.status_code == 409, response.text
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert (
            account is not None
            and account.status == status
            and account.routing_policy == "preserve"
            and not account.routing_held
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["routing-policy", "pause", "reactivate"])
async def test_matching_caller_expectation_allows_intended_routing_write(async_client, action):
    status = AccountStatus.PAUSED if action == "reactivate" else AccountStatus.ACTIVE
    account_id = await _seed(status)
    payload = {"expected": _expected(status)}
    if action == "routing-policy":
        payload["routingPolicy"] = "burn_first"
    response = await (async_client.put if action == "routing-policy" else async_client.post)(
        f"/api/accounts/{account_id}/{action}", json=payload
    )
    assert response.status_code == 200, response.text
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        assert account.routing_policy == ("burn_first" if action == "routing-policy" else "normal")
        assert account.status == (AccountStatus.PAUSED if action == "pause" else AccountStatus.ACTIVE)
