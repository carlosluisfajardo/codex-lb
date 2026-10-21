from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.clients.proxy import ProxyResponseError
from app.modules.proxy import service as proxy
from app.modules.proxy._service.http_bridge.helpers import _HTTPBridgeRequiredOwnerReplayForbiddenError
from app.modules.proxy._service.http_bridge.session_registry import _HTTPBridgeCreationFuture
from app.modules.proxy._service.http_bridge.streaming import _http_bridge_capacity_wait_plan
from tests.unit.test_required_owner_recovery import _bridge, _create

pytestmark = pytest.mark.unit


def _joining(service: Any, owner: Any, **changes: Any) -> Any:
    kwargs = dict(
        headers={},
        affinity=proxy._AffinityPolicy(),
        api_key=None,
        request_model=None,
        previous_response_id="resp-original",
        allow_previous_response_recovery_rebind=True,
        idle_ttl_seconds=120,
        max_sessions=8,
        preferred_account_id=owner.id,
        preferred_account_has_continuity_provenance=True,
        fallback_on_preferred_account_unavailable=False,
        request_deadline=120,
    )
    key = changes.pop("key", proxy._HTTPBridgeSessionKey("request", "shared-recovery", None))
    kwargs.update(changes)
    return service._get_or_create_http_bridge_session(key, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("fence", ["followup", "attempted"])
@pytest.mark.parametrize("recover_after_join", [False, True])
async def test_join_fence_tracks_actual_creator_recovery(monkeypatch, fence, recover_after_join) -> None:
    service, owner, scheduler, clock, opened = _bridge(monkeypatch, blocked="none")
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    pause, proceed = asyncio.Event(), asyncio.Event()
    if recover_after_join:
        service._load_balancer._runtime[owner.id].cooldown_until = clock.time() + 4
        real_select = service._load_balancer.select_account

        async def delayed_selection(**kwargs):
            pause.set()
            await proceed.wait()
            return await real_select(**kwargs)

        monkeypatch.setattr(service._load_balancer, "select_account", delayed_selection)
    else:
        upstream = opened.return_value

        async def delayed_open(*args, **kwargs):
            pause.set()
            await proceed.wait()
            return upstream

        opened.side_effect = delayed_open
    creator = scheduler.create_task(_joining(service, owner))
    try:
        await pause.wait()
        kwargs = (
            {"allow_required_owner_recovery": False} if fence == "followup" else {"response_create_attempt_count": 1}
        )
        joiner = scheduler.create_task(_joining(service, owner, **kwargs))
        await scheduler.drain()
        assert not joiner.done(), "healthy/unclassified shared creation must not be fenced as recovering"
        proceed.set()
        await scheduler.drain()
        if recover_after_join:
            assert joiner.done(), "a join begun before actual recovery must still be fenced"
            with pytest.raises(_HTTPBridgeRequiredOwnerReplayForbiddenError):
                await joiner
            assert not creator.done()
            await scheduler.advance(4)
        session = await creator
        if not recover_after_join:
            assert await joiner is session
        assert opened.await_count == 1
        assert not session.closed
        assert service._http_bridge_sessions[session.key] is session
        await service._load_balancer.release_account_lease(session.account_lease)
        assert not service._load_balancer._runtime[owner.id].leases
    finally:
        proceed.set()
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("waiter", ["same_key", "short_deadline", "capacity", "cancelled"])
async def test_waiter_does_not_discard_creator_recovering_beyond_ten_seconds(monkeypatch, waiter) -> None:
    service, owner, scheduler, clock, opened = _bridge(monkeypatch)
    service._load_balancer._runtime[owner.id].cooldown_until = clock.time() + 12
    monkeypatch.setattr(service, "_claim_durable_http_bridge_session", AsyncMock())
    assert proxy._proxy_admission_wait_timeout_seconds() == 10.0
    creator = scheduler.create_task(_joining(service, owner))
    try:
        await scheduler.drain()
        inflight = next(iter(service._http_bridge_inflight_sessions.values()))
        kwargs: dict[str, Any] = {}
        if waiter in ("short_deadline", "capacity"):
            kwargs["request_deadline"] = 5
        if waiter == "capacity":
            kwargs.update(key=proxy._HTTPBridgeSessionKey("request", "unrelated", None), max_sessions=1)
        joiner = scheduler.create_task(_joining(service, owner, **kwargs))
        await scheduler.drain()
        if waiter == "cancelled":
            joiner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await joiner
        await scheduler.advance(10)
        assert not creator.done() and not inflight.done(), "a waiter must not fail or evict the creator's future"
        assert inflight in service._http_bridge_inflight_sessions.values()
        if waiter in ("short_deadline", "capacity"):
            assert joiner.done(), "the joiner's own request deadline must bound waiting"
            with pytest.raises(ProxyResponseError) as caught:
                await joiner
            assert caught.value.status_code == 429
        elif waiter == "same_key":
            assert not joiner.done(), "the fixed ten-second wait must not truncate creator recovery"
        await scheduler.advance(4)
        assert creator.done(), "recovering creation must finish when its owner returns"
        session = await creator
        if waiter == "same_key":
            assert await joiner is session
        assert opened.await_count == 1
        assert opened.return_value.close.await_count == 0
        assert service._http_bridge_sessions[session.key] is session
        await service._load_balancer.release_account_lease(session.account_lease)
        assert not service._load_balancer._runtime[owner.id].leases
    finally:
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("followup", [False, True])
async def test_local_capacity_keeps_outer_wait_without_owner_window(monkeypatch, followup) -> None:
    service, owner, scheduler, clock, opened = _bridge(monkeypatch, blocked="capacity")
    task = scheduler.create_task(_create(service, owner, allow_required_owner_recovery=not followup))
    try:
        await scheduler.drain()
        assert task.done(), "local capacity must return to the existing outer request-deadline loop"
        with pytest.raises(ProxyResponseError) as caught:
            await task
        assert caught.value.status_code == 429
        plan = _http_bridge_capacity_wait_plan(caught.value, request_deadline=7200, now=0)
        assert plan is not None and plan[:2] == (30, 30)
        short_plan = _http_bridge_capacity_wait_plan(caught.value, request_deadline=5, now=0)
        assert short_plan is not None and short_plan[:2] == (5, 30)
        assert opened.await_count == 0 and clock.monotonic() == 0
    finally:
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_owner_selection_churn_is_bounded_without_extending_window(monkeypatch) -> None:
    service, owner, scheduler, clock, opened = _bridge(monkeypatch)
    service._load_balancer._runtime[owner.id].cooldown_until = clock.time() + 120
    select = AsyncMock(wraps=service._load_balancer.select_account)
    monkeypatch.setattr(service._load_balancer, "select_account", select)
    task = scheduler.create_task(_create(service, owner))
    try:
        await scheduler.drain()
        await scheduler.advance(30)
        assert task.done(), "rechecks must not renew the original recovery window"
        with pytest.raises(ProxyResponseError):
            await task
        assert select.await_count <= 6, "recovery must not run fifteen full selector passes in thirty seconds"
        assert opened.await_count == 0
        assert not service._load_balancer._runtime[owner.id].leases
    finally:
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("fence", ["followup", "attempted"])
async def test_joiner_keeps_own_send_provenance_when_creator_hits_capacity(monkeypatch, fence) -> None:
    service, owner, scheduler, clock, opened = _bridge(monkeypatch, blocked="capacity")
    entered, proceed = asyncio.Event(), asyncio.Event()
    select = service._load_balancer.select_account

    async def paused_selection(**kwargs):
        entered.set()
        await proceed.wait()
        return await select(**kwargs)

    monkeypatch.setattr(service._load_balancer, "select_account", paused_selection)
    creator = scheduler.create_task(_joining(service, owner))
    try:
        await entered.wait()
        kwargs = (
            {"response_create_attempt_count": 1} if fence == "attempted" else {"allow_required_owner_recovery": False}
        )
        joiner = scheduler.create_task(_joining(service, owner, **kwargs))
        await scheduler.drain()
        assert not joiner.done()
        proceed.set()
        await scheduler.drain()
        with pytest.raises(ProxyResponseError) as creator_error:
            await creator
        with pytest.raises(ProxyResponseError) as joiner_error:
            await joiner
        assert creator_error.value.status_code == joiner_error.value.status_code == 429
        assert _http_bridge_capacity_wait_plan(creator_error.value, request_deadline=120, now=0) is not None
        if fence == "attempted":
            assert isinstance(joiner_error.value, _HTTPBridgeRequiredOwnerReplayForbiddenError)
            assert _http_bridge_capacity_wait_plan(joiner_error.value, request_deadline=120, now=0) is None
        else:
            assert _http_bridge_capacity_wait_plan(joiner_error.value, request_deadline=120, now=0) is not None
        assert opened.await_count == 0
        assert not service._http_bridge_inflight_sessions
        assert all(not state.leases for state in service._load_balancer._runtime.values())
    finally:
        proceed.set()
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_superseded_creator_cannot_mark_replacement_as_recovering(monkeypatch) -> None:
    service, owner, scheduler, clock, opened = _bridge(monkeypatch)
    entered, proceed = asyncio.Event(), asyncio.Event()
    create = service._create_http_bridge_session
    key = proxy._HTTPBridgeSessionKey("request", "shared-recovery", None)

    async def pause_creation(*args, **kwargs):
        entered.set()
        await proceed.wait()
        return await create(*args, **kwargs)

    monkeypatch.setattr(service, "_create_http_bridge_session", pause_creation)
    creator = scheduler.create_task(_joining(service, owner))
    replacement = _HTTPBridgeCreationFuture(120)
    try:
        await entered.wait()
        original = service._http_bridge_inflight_sessions[key]
        assert isinstance(original, _HTTPBridgeCreationFuture)
        original_view = original.for_waiter(allow_recovery=False)
        replacement_view = replacement.for_waiter(allow_recovery=False)
        # Force replacement of a stale marker, as in the existing superseded-
        # creator fixtures; the old creator must retain its own future identity.
        async with service._http_bridge_lock:
            service._http_bridge_inflight_sessions[key] = replacement
        proceed.set()
        await scheduler.drain()
        assert not replacement_view.done(), "a stale creator cannot fence the new creator's healthy waiters"
        assert original_view.done(), "actual recovery must mark the original creator's own future"
        with pytest.raises(_HTTPBridgeRequiredOwnerReplayForbiddenError):
            await original_view
        creator.cancel()
        with pytest.raises(asyncio.CancelledError):
            await creator
        assert service._http_bridge_inflight_sessions[key] is replacement
        assert not replacement.done()
        assert opened.await_count == 0
        assert all(not state.leases for state in service._load_balancer._runtime.values())
    finally:
        proceed.set()
        replacement.cancel()
        await scheduler.cancel_owned_tasks()
