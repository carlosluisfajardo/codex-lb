from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.clients.proxy import ProxyResponseError
from app.db.models import DashboardSettings, HttpBridgeSessionState
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


def _proof_joiner_harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    service, owner, scheduler, clock, opened = _bridge(monkeypatch, blocked="cooldown")
    service._load_balancer._runtime[owner.id].cooldown_until = clock.time() + 120
    settings = DashboardSettings(
        prefer_earlier_reset_accounts=False,
        routing_strategy="usage_weighted",
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=1800,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
        http_responses_session_bridge_gateway_safe_mode=False,
    )
    monkeypatch.setattr(proxy, "get_settings_cache", lambda: SimpleNamespace(get=AsyncMock(return_value=settings)))
    historical_input: list[proxy.JsonValue] = [
        {"role": "user", "content": [{"type": "input_text", "text": "old question"}]}
    ]
    full_history = proxy.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "previous_response_id": "resp_required_anchor",
            "input": [
                *historical_input,
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "old answer"}],
                },
                {"role": "user", "content": [{"type": "input_text", "text": "next question"}]},
            ],
        }
    )
    incomplete = full_history.model_copy(update={"input": "next question"})
    lookup = proxy.DurableBridgeLookup(
        session_id="shared-owner",
        canonical_kind="session_header",
        canonical_key="shared-owner",
        api_key_scope="__anonymous__",
        account_id=owner.id,
        owner_instance_id=proxy.get_settings().http_responses_session_bridge_instance_id,
        owner_epoch=1,
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=60),
        state=HttpBridgeSessionState.ACTIVE,
        latest_turn_state="shared-owner",
        latest_response_id=full_history.previous_response_id,
        latest_input_item_count=len(historical_input),
        latest_input_full_fingerprint=proxy._fingerprint_input_items(historical_input),
        model=full_history.model,
    )
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=lookup))
    monkeypatch.setattr(service._durable_bridge, "lookup_recovery_attempt", AsyncMock(return_value=None))
    monkeypatch.setattr(
        service._durable_bridge, "retire_continuity_owner_if_unavailable", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(service, "_http_bridge_has_live_local_session", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_http_bridge_can_forward_to_active_owner", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_resolve_file_account_for_responses", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_resolve_websocket_previous_response_owner", AsyncMock(return_value=owner.id))
    instance = proxy.get_settings().http_responses_session_bridge_instance_id
    monkeypatch.setattr(proxy, "_http_bridge_owner_instance", AsyncMock(return_value=instance))
    monkeypatch.setattr(proxy, "_active_http_bridge_instance_ring", AsyncMock(return_value=(instance, (instance,))))
    first_selection = asyncio.Event()
    real_select = service._load_balancer.select_account

    async def observe_selection(**kwargs: Any) -> Any:
        selection = await real_select(**kwargs)
        first_selection.set()
        return selection

    monkeypatch.setattr(service._load_balancer, "select_account", AsyncMock(side_effect=observe_selection))
    get_or_create = service._get_or_create_http_bridge_session
    attempts: dict[str, list[tuple[Any, ...]]] = {}

    async def observe_creation(key: Any, **kwargs: Any) -> Any:
        task = asyncio.current_task()
        name = task.get_name() if task is not None else "?"
        attempts.setdefault(name, []).append(
            (
                kwargs["preferred_account_id"],
                kwargs["previous_response_id"],
                frozenset(kwargs["exclude_account_ids"] or ()),
                kwargs["allow_required_owner_recovery"],
            )
        )
        if kwargs["preferred_account_id"] is None:
            # Bound the account-neutral replay after recording it: no upstream
            # dispatch is permitted in this owning regression fixture.
            raise ProxyResponseError(599, {"error": {"code": "bounded_test_replay", "message": "bounded"}})
        return await get_or_create(key, **kwargs)

    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", observe_creation)

    def stream(payload: Any, name: str, **stream_options: Any) -> asyncio.Task[Any]:
        async def consume() -> None:
            async for _ in service._stream_via_http_bridge(
                payload,
                headers={"x-codex-session-id": "shared-owner"},
                codex_session_affinity=True,
                propagate_http_errors=True,
                openai_cache_affinity=True,
                api_key=None,
                api_key_reservation=None,
                suppress_text_done_events=False,
                idle_ttl_seconds=120,
                codex_idle_ttl_seconds=1800,
                max_sessions=8,
                queue_limit=4,
                **stream_options,
            ):
                pass

        task = scheduler.create_task(consume())
        task.set_name(name)
        return task

    return dict(
        service=service,
        owner=owner,
        scheduler=scheduler,
        clock=clock,
        opened=opened,
        full_history=full_history,
        incomplete=incomplete,
        attempts=attempts,
        first_selection=first_selection,
        stream=stream,
    )


async def _settle(scheduler: Any, task: asyncio.Task[Any]) -> None:
    for _ in range(200):
        await scheduler.drain()
        if task.done():
            return
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("fence", ["attempted", "sent", "operation", "journal", "output", "incomplete", "mismatch"])
async def test_proof_joiner_cannot_replay_effects_or_unproven_history(monkeypatch, fence) -> None:
    h = _proof_joiner_harness(monkeypatch)
    service, scheduler = h["service"], h["scheduler"]
    creator = h["stream"](h["incomplete"], "creator-without-proof")
    try:
        await asyncio.wait_for(h["first_selection"].wait(), 5)
        await scheduler.drain()
        (future,) = service._http_bridge_inflight_sessions.values()
        assert not future.done() and not creator.done()
        original_deadline = future.request_deadline
        payload = h["full_history"].model_copy(deep=True)
        if fence == "incomplete":
            payload.input = "unproven new turn"
        elif fence == "mismatch":
            payload.input[0]["content"][0]["text"] = "not the stored history"
        elif fence == "journal":
            monkeypatch.setattr(service._durable_bridge, "lookup_recovery_attempt", AsyncMock(return_value=object()))
        else:
            prepare = service._prepare_http_bridge_request

            def retained_provenance(*args, **kwargs):
                state, text = prepare(*args, **kwargs)
                # Keep the real full-history proof and bridge path. Only the
                # retained same-operation provenance is supplied at this seam.
                if fence == "attempted":
                    state.response_create_attempt_count = 1
                    state.response_create_sent_at = None
                elif fence == "sent":
                    state.response_create_sent_at = 0
                elif fence == "operation":
                    state.operation_id = "known-existing-operation"
                    state.operation_registered = True
                elif fence == "output":
                    state.response_event_count = 1
                    state.downstream_visible = True
                return state, text

            monkeypatch.setattr(service, "_prepare_http_bridge_request", retained_provenance)
        joiner = h["stream"](payload, "proof-holder")
        await _settle(scheduler, joiner)
        assert len(h["attempts"]["proof-holder"]) == 1, "fenced or unproven B must not enter account-neutral replay"
        if fence in ("incomplete", "mismatch"):
            assert not joiner.done(), "unproven history keeps its existing bounded same-owner wait"
        else:
            with pytest.raises(_HTTPBridgeRequiredOwnerReplayForbiddenError):
                await joiner
        assert h["opened"].await_count == 0
        assert not creator.done() and not future.done()
        assert future in service._http_bridge_inflight_sessions.values()
        assert future.request_deadline == original_deadline
        assert h["clock"].monotonic() == 0
    finally:
        await scheduler.cancel_owned_tasks()
    assert all(not runtime.leases for runtime in service._load_balancer._runtime.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("fence", ["incomplete", "mismatch", "file_pin", "attempted"])
async def test_mixed_temporary_capacity_refusal_keeps_independent_replay_guards(monkeypatch, fence) -> None:
    h = _proof_joiner_harness(monkeypatch)
    service, scheduler = h["service"], h["scheduler"]
    runtime = service._load_balancer._runtime[h["owner"].id]
    runtime.inflight_streams = 1
    payload = h["full_history"].model_copy(deep=True)
    if fence == "incomplete":
        payload.input = "delta only"
    elif fence == "mismatch":
        payload.input[0]["content"][0]["text"] = "different history"
    elif fence != "file_pin":
        prepare = service._prepare_http_bridge_request

        def attempted(*args, **kwargs):
            state, text = prepare(*args, **kwargs)
            state.response_create_attempt_count = 1
            state.response_create_sent_at = None
            return state, text

        monkeypatch.setattr(service, "_prepare_http_bridge_request", attempted)
    task = h["stream"](
        payload, "fenced-capacity", rewritten_file_account_id=h["owner"].id if fence == "file_pin" else None
    )
    try:
        await asyncio.wait_for(h["first_selection"].wait(), 5)
        await scheduler.drain()
        if fence != "attempted":
            assert not task.done()
            await scheduler.advance(30)
        await _settle(scheduler, task)
        assert len(h["attempts"]["fenced-capacity"]) == 1, "temporary capacity provenance cannot replace replay proof"
        with pytest.raises(ProxyResponseError) as caught:
            await asyncio.wait_for(task, 1)
        assert caught.value.status_code == 429
        assert caught.value.payload["error"]["code"] == "account_stream_cap"
        if fence == "attempted":
            assert isinstance(caught.value, _HTTPBridgeRequiredOwnerReplayForbiddenError)
        assert payload.previous_response_id == "resp_required_anchor"
        assert h["opened"].await_count == 0
        assert all(not state.leases for state in service._load_balancer._runtime.values())
        assert not service._http_bridge_inflight_sessions
    finally:
        await scheduler.cancel_owned_tasks()
