from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.core.balancer import AccountState, SelectionResult, select_account
from app.core.clients.proxy import ProxyResponseError
from app.core.config.settings import Settings
from app.db.models import AccountStatus, DashboardSettings, HttpBridgeSessionState, StickySessionKind
from app.modules.proxy import load_balancer as lb
from app.modules.proxy import service as proxy
from app.modules.proxy._service.http_bridge.request_submit import _send_http_bridge_request_text_with_archive_id
from app.modules.proxy._service.http_bridge.streaming import _http_bridge_capacity_wait_plan
from app.modules.proxy.account_cache import AccountSelectionCache
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler
from tests.unit.test_load_balancer_contract import _account, _balancer
from tests.unit.test_proxy_http_bridge import _make_bridge_session

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("status", "extra", "reason"),
    [
        (AccountStatus.QUOTA_EXCEEDED, {}, "quota"),
        (AccountStatus.RATE_LIMITED, {}, "rate_limit"),
        (AccountStatus.PAUSED, {}, "paused"),
        (AccountStatus.DEACTIVATED, {}, "deactivated"),
        (AccountStatus.REAUTH_REQUIRED, {"access_token_expires_at": 50}, "authentication"),
        (AccountStatus.ACTIVE, {"cooldown_until": 200}, "cooldown"),
        (AccountStatus.ACTIVE, {"error_count": 3, "last_error_at": 99}, "error_backoff"),
        (AccountStatus.QUOTA_EXCEEDED, {"cooldown_until": 200}, "quota"),
        (AccountStatus.RATE_LIMITED, {"cooldown_until": 200}, "rate_limit"),
        (
            AccountStatus.REAUTH_REQUIRED,
            {"access_token_expires_at": 50, "cooldown_until": 200},
            "authentication",
        ),
    ],
)
def test_core_required_owner_failure_reason(status: AccountStatus, extra: dict[str, Any], reason: str) -> None:
    result = select_account([AccountState("owner", status, **extra)], now=100, allow_usage_exhaustion_error=False)
    assert result.account is None
    assert getattr(result, "owner_failure_reason", None) == reason


@pytest.mark.parametrize("factory", [SelectionResult, lb.AccountSelection])
def test_legacy_failure_constructors_default_to_unknown(factory: Any) -> None:
    assert factory(None, "unexplained").owner_failure_reason == "unknown"


def test_core_unknown_or_mixed_pool_failure_cannot_prove_one_temporary_owner() -> None:
    for states in (
        [],
        [AccountState("a", AccountStatus.ACTIVE, cooldown_until=200), AccountState("b", AccountStatus.QUOTA_EXCEEDED)],
    ):
        result = select_account(states, now=100, allow_usage_exhaustion_error=False)
        assert result.account is None
        assert result.owner_failure_reason == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("hard_sticky", [False, True])
@pytest.mark.parametrize("blocked", ["cooldown", "error_backoff", "quota", "rate_limit", "paused", "deactivated"])
@pytest.mark.parametrize("capped", [False, True])
async def test_real_selector_preserves_owner_failure_with_mixed_capacity(
    monkeypatch: pytest.MonkeyPatch, hard_sticky: bool, blocked: str, capped: bool
) -> None:
    owner = _account("typed-owner")
    alternate = _account("alternate")
    statuses = {
        "quota": AccountStatus.QUOTA_EXCEEDED,
        "rate_limit": AccountStatus.RATE_LIMITED,
        "paused": AccountStatus.PAUSED,
        "deactivated": AccountStatus.DEACTIVATED,
    }
    owner.status = statuses.get(blocked, AccountStatus.ACTIVE)
    balancer, _, _, sticky = _balancer([owner, alternate], AccountSelectionCache(ttl_seconds=60))
    sticky.account_id = owner.id
    now = time.time()
    balancer._runtime[owner.id] = lb.RuntimeState(
        cooldown_until=now + 100 if blocked == "cooldown" else None,
        error_count=3 if blocked == "error_backoff" else 0,
        last_error_at=now if blocked == "error_backoff" else None,
        inflight_streams=1 if capped else 0,
    )
    monkeypatch.setattr(lb, "set_degraded", lambda _reason: None)
    monkeypatch.setattr(lb, "set_normal", lambda: None)
    selection = await balancer.select_account(
        sticky_key="typed-sticky" if hard_sticky else None,
        sticky_kind=StickySessionKind.CODEX_SESSION if hard_sticky else None,
        required_account_id=None if hard_sticky else owner.id,
        required_continuity_owner=not hard_sticky,
        lease_kind="stream",
        concurrency_caps=lb.AccountConcurrencyCaps(response_create_limit=1, stream_limit=1),
        allow_usage_exhaustion_error=False,
    )
    assert selection.account is None
    assert selection.lease is None
    assert getattr(selection, "owner_failure_reason", None) == blocked
    assert sticky.account_id == owner.id
    assert balancer._runtime[owner.id].inflight_streams == int(capped)


@pytest.mark.asyncio
async def test_hard_affinity_preserves_core_reset_metadata() -> None:
    owner = _account("quota-owner")
    owner.status = AccountStatus.QUOTA_EXCEEDED
    owner.reset_at = int(time.time()) + 300
    balancer, _, _, sticky = _balancer([owner], AccountSelectionCache(ttl_seconds=60))
    sticky.account_id = owner.id
    result = await balancer.select_account(
        sticky_key="typed-sticky", sticky_kind=StickySessionKind.CODEX_SESSION, allow_usage_exhaustion_error=False
    )
    assert result.error_code == "hard_affinity_saturated"
    assert result.resets_at == owner.reset_at
    assert getattr(result, "owner_failure_reason", None) == "quota"


@pytest.mark.asyncio
@pytest.mark.parametrize("hard_sticky", [False, True])
@pytest.mark.parametrize("quota", [False, True])
async def test_required_owner_fair_share_requires_positive_eligibility(hard_sticky: bool, quota: bool) -> None:
    owner = _account("fair-share-owner")
    owner.status = AccountStatus.QUOTA_EXCEEDED if quota else AccountStatus.ACTIVE
    balancer, _, _, sticky = _balancer([owner], AccountSelectionCache(ttl_seconds=60))
    sticky.account_id = owner.id
    balancer._runtime[owner.id] = lb.RuntimeState(inflight_streams=2, stream_key_inflight={"k1": 2})
    result = await balancer.select_account(
        sticky_key="typed-fair-share" if hard_sticky else None,
        sticky_kind=StickySessionKind.CODEX_SESSION if hard_sticky else None,
        required_account_id=None if hard_sticky else owner.id,
        required_continuity_owner=not hard_sticky,
        lease_kind="stream",
        concurrency_caps=lb.AccountConcurrencyCaps(response_create_limit=1, stream_limit=1),
        api_key_id="k1",
        api_key_stream_fair_share_threshold_pct=100,
    )
    assert result.account is None
    assert result.error_code == "api_key_stream_fair_share"
    assert result.owner_failure_reason == ("quota" if quota else "local_contention")


@pytest.mark.asyncio
async def test_owner_absence_and_policy_are_explicit() -> None:
    owner, alternate = _account("scoped-owner"), _account("scoped-alternate")
    balancer, _, _, _ = _balancer([owner, alternate], AccountSelectionCache(ttl_seconds=60))
    policy = await balancer.select_account(
        required_account_id=owner.id,
        required_continuity_owner=True,
        account_ids={alternate.id},
    )
    absent = await balancer.select_account(required_account_id="missing", required_continuity_owner=True)
    assert policy.owner_failure_reason == "policy"
    assert absent.owner_failure_reason == "absent"


def _bridge(monkeypatch: pytest.MonkeyPatch, *, blocked: str = "cooldown") -> tuple[Any, ...]:
    clock = VirtualClock(epoch_value=time.time())
    scheduler = VirtualScheduler(clock)
    owner, alternate = _account("bridge-owner"), _account("bridge-alternate")
    statuses = {
        "quota": AccountStatus.QUOTA_EXCEEDED,
        "rate_limit": AccountStatus.RATE_LIMITED,
        "paused": AccountStatus.PAUSED,
        "deactivated": AccountStatus.DEACTIVATED,
        "authentication": AccountStatus.REAUTH_REQUIRED,
    }
    owner.status = statuses.get(blocked, AccountStatus.ACTIVE)
    balancer, accounts, _, _ = _balancer([owner, alternate], AccountSelectionCache(ttl_seconds=60))
    balancer._clock = clock
    balancer._runtime[owner.id] = lb.RuntimeState(
        cooldown_until=clock.time() + 4 if blocked == "cooldown" else None,
        error_count=3 if blocked == "error_backoff" else 0,
        last_error_at=clock.time() if blocked == "error_backoff" else None,
        inflight_streams=1 if blocked in ("capacity", *statuses) else 0,
    )
    if blocked == "absent":
        accounts.accounts.remove(owner)
    monkeypatch.setattr(lb, "account_access_token_expires_at", lambda *_: clock.time() - 1)
    monkeypatch.setattr(time, "time", clock.time)
    monkeypatch.setattr(lb, "set_degraded", lambda _reason: None)
    monkeypatch.setattr(lb, "set_normal", lambda: None)
    settings = Settings(proxy_account_stream_limit=1, proxy_account_stream_recovery_reserve=0)
    row = DashboardSettings(prefer_earlier_reset_accounts=False, routing_strategy="usage_weighted")
    monkeypatch.setattr(proxy, "get_settings", lambda: settings)
    monkeypatch.setattr(lb, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy, "get_settings_cache", lambda: SimpleNamespace(get=AsyncMock(return_value=row)))
    service = proxy.ProxyService(cast(Any, SimpleNamespace()), clock=clock, scheduler=scheduler)
    service._load_balancer = balancer
    upstream = SimpleNamespace(response_header=lambda _: None, close=AsyncMock())
    open_upstream = AsyncMock(return_value=upstream)
    monkeypatch.setattr(service, "_open_upstream_websocket_with_budget", open_upstream)
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", AsyncMock(side_effect=lambda account, **_: account))
    monkeypatch.setattr(service, "_relay_http_bridge_upstream_messages", AsyncMock())
    return service, owner, scheduler, clock, open_upstream


async def _create(service: Any, owner: Any, *, budget: float = 120, **extra: Any) -> Any:
    return await service._create_http_bridge_session(
        proxy._HTTPBridgeSessionKey("session_header", "typed-owner-bridge", None),
        headers={"x-codex-session-id": "typed-owner-bridge"},
        affinity=proxy._AffinityPolicy(),
        api_key=None,
        request_model=None,
        idle_ttl_seconds=120,
        request_stage="follow_up",
        preferred_account_id=owner.id,
        require_preferred_account=True,
        preferred_account_is_continuity_owner=True,
        request_deadline=service._clock.monotonic() + budget,
        **extra,
    )


async def _prompt_refusal(service: Any, owner: Any, scheduler: VirtualScheduler, **extra: Any) -> ProxyResponseError:
    task = scheduler.create_task(_create(service, owner, **extra))
    try:
        await scheduler.drain()
        assert task.done(), "terminal, unknown, or already-dispatched owner must refuse without a recovery wait"
        with pytest.raises(ProxyResponseError) as caught:
            await task
        return caught.value
    finally:
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["cooldown", "error_backoff", "capacity"])
async def test_actual_selector_to_bridge_waits_and_recovers_one_owner(
    monkeypatch: pytest.MonkeyPatch, blocked: str
) -> None:
    service, owner, scheduler, clock, opened = _bridge(monkeypatch, blocked=blocked)
    task = scheduler.create_task(_create(service, owner))
    try:
        await scheduler.drain()
        assert opened.await_count == 0, "no alternate owner may open while the required owner is blocked"
        assert not task.done(), "temporary required-owner failure must wait before dispatch"
        if blocked == "capacity":
            service._load_balancer._runtime[owner.id].inflight_streams = 0
        elif blocked == "error_backoff":
            service._load_balancer._runtime[owner.id].last_error_at = clock.time() - 31
        await scheduler.advance(4)
        session = await task
        assert session.account.id == owner.id
        assert opened.await_count == 1
        assert opened.await_args.args[0].id == owner.id
        await service._load_balancer.release_account_lease(session.account_lease)
        assert service._load_balancer._runtime[owner.id].inflight_streams == 0
    finally:
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["quota", "rate_limit", "paused", "deactivated", "authentication", "absent"])
async def test_actual_selector_to_bridge_terminal_mixed_refusal_has_no_outer_wait(
    monkeypatch: pytest.MonkeyPatch, blocked: str
) -> None:
    service, owner, scheduler, clock, opened = _bridge(monkeypatch, blocked=blocked)
    refusal = await _prompt_refusal(service, owner, scheduler)
    assert clock.monotonic() == 0
    assert opened.await_count == 0
    assert _http_bridge_capacity_wait_plan(refusal, request_deadline=120, now=0) is None
    assert all(not runtime.leases for runtime in service._load_balancer._runtime.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("budget, expiry", [(120, 30), (5, 5)])
async def test_actual_selector_to_bridge_one_recovery_window(
    monkeypatch: pytest.MonkeyPatch, budget: float, expiry: float
) -> None:
    service, owner, scheduler, clock, opened = _bridge(monkeypatch, blocked="capacity")
    task = scheduler.create_task(_create(service, owner, budget=budget))
    try:
        await scheduler.drain()
        assert not task.done(), "temporary contention must enter the bounded window"
        await scheduler.advance(expiry)
        assert task.done(), "reselection must not renew the recovery window"
        with pytest.raises(ProxyResponseError) as caught:
            await task
        assert opened.await_count == 0
        assert clock.monotonic() == expiry
        assert _http_bridge_capacity_wait_plan(caught.value, request_deadline=120, now=expiry) is None
        assert all(not runtime.leases for runtime in service._load_balancer._runtime.values())
    finally:
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("late_success", [False, True])
async def test_reselection_itself_obeys_recovery_deadline_and_releases_lease(
    monkeypatch: pytest.MonkeyPatch, late_success: bool
) -> None:
    service, owner, scheduler, clock, opened = _bridge(monkeypatch, blocked="capacity")
    balancer = service._load_balancer
    persist = balancer._persist_selection_state
    calls = 0

    async def slow_persist(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            if late_success:
                # Model synchronous work that returns after the timer deadline.
                clock.advance(31)
            else:
                await scheduler.sleep(100)
        return await persist(*args, **kwargs)

    monkeypatch.setattr(balancer, "_persist_selection_state", slow_persist)
    task = scheduler.create_task(_create(service, owner))
    try:
        await scheduler.drain()
        balancer._runtime[owner.id].inflight_streams = 0
        await scheduler.advance(2 if late_success else 30)
        assert task.done(), "the recovery deadline must bound selection, not only sleeps"
        with pytest.raises(ProxyResponseError):
            await task
        assert opened.await_count == 0
        assert balancer._runtime[owner.id].inflight_streams == 0
        assert all(not runtime.leases for runtime in balancer._runtime.values())
    finally:
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat_cancel", [False, True])
async def test_late_selection_cancellation_releases_lease_while_runtime_locked(
    monkeypatch: pytest.MonkeyPatch, repeat_cancel: bool
) -> None:
    service, owner, scheduler, clock, opened = _bridge(monkeypatch, blocked="capacity")
    balancer = service._load_balancer
    select = balancer.select_account
    release = balancer.release_account_lease
    releasing_late_lease = asyncio.Event()
    held_lock = False
    selections = 0

    async def late_selection(**kwargs: Any) -> lb.AccountSelection:
        nonlocal held_lock, selections
        selection = await select(**kwargs)
        selections += 1
        if selection.account is not None:
            assert selection.account.id == owner.id
            assert selection.lease is not None
            # The real selector has acquired its lease. Synchronous work then
            # crosses expiry, and another holder occupies the runtime lock.
            clock.advance(31)
            await balancer._runtime_lock.acquire()
            held_lock = True
        return selection

    async def observe_release(lease: lb.AccountLease | None) -> None:
        if lease is not None:
            releasing_late_lease.set()
        await release(lease)

    monkeypatch.setattr(balancer, "select_account", late_selection)
    monkeypatch.setattr(balancer, "release_account_lease", observe_release)
    key = proxy._HTTPBridgeSessionKey("request", "cancel-late-owner-selection", None)
    task = scheduler.create_task(
        service._get_or_create_http_bridge_session(
            key,
            headers={},
            affinity=proxy._AffinityPolicy(),
            api_key=None,
            request_model=None,
            idle_ttl_seconds=120,
            max_sessions=8,
            preferred_account_id=owner.id,
            preferred_account_has_continuity_provenance=True,
            fallback_on_preferred_account_unavailable=False,
        )
    )
    try:
        await scheduler.drain()
        inflight = service._http_bridge_inflight_sessions[key]
        balancer._runtime[owner.id].inflight_streams = 0
        await scheduler.advance(2)
        assert selections == 2
        assert clock.monotonic() == 33
        assert releasing_late_lease.is_set()
        assert held_lock
        assert balancer._runtime[owner.id].inflight_streams == 1
        assert len(balancer._runtime[owner.id].leases) == 1
        task.cancel()
        await scheduler.drain()
        if repeat_cancel:
            task.cancel()
            await scheduler.drain()
        assert not task.done(), "cancellation must wait for the late selection's owned lease cleanup"
        balancer._runtime_lock.release()
        held_lock = False
        await scheduler.drain()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert inflight.cancelled()
        assert not service._http_bridge_inflight_sessions
        assert not service._http_bridge_sessions
        assert opened.await_count == 0
        assert balancer._runtime[owner.id].inflight_streams == 0
        assert all(not runtime.leases for runtime in balancer._runtime.values())
    finally:
        if held_lock:
            balancer._runtime_lock.release()
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat_cancel", [False, True])
async def test_wait_cancellation_settles_singleflight_even_while_registry_locked(
    monkeypatch: pytest.MonkeyPatch, repeat_cancel: bool
) -> None:
    service, owner, scheduler, _, opened = _bridge(monkeypatch)
    key = proxy._HTTPBridgeSessionKey("request", "cancel-owner-wait", None)
    task = scheduler.create_task(
        service._get_or_create_http_bridge_session(
            key,
            headers={},
            affinity=proxy._AffinityPolicy(),
            api_key=None,
            request_model=None,
            idle_ttl_seconds=120,
            max_sessions=8,
            preferred_account_id=owner.id,
            preferred_account_has_continuity_provenance=True,
            fallback_on_preferred_account_unavailable=False,
        )
    )
    try:
        await scheduler.drain()
        assert key in service._http_bridge_inflight_sessions
        inflight = service._http_bridge_inflight_sessions[key]
        if repeat_cancel:
            async with service._http_bridge_lock:
                task.cancel()
                await scheduler.drain()
                task.cancel()
                await scheduler.drain()
                assert not task.done(), "repeated cancellation cannot abandon owned registry cleanup"
        else:
            task.cancel()
        await scheduler.drain()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert inflight.cancelled()
        assert not service._http_bridge_inflight_sessions
        assert not service._http_bridge_sessions
        assert opened.await_count == 0
        assert all(not runtime.leases for runtime in service._load_balancer._runtime.values())
    finally:
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_ambiguous_send_retained_attempt_fences_owner_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    service, owner, scheduler, clock, opened = _bridge(monkeypatch)
    session = _make_bridge_session()
    send = AsyncMock(side_effect=RuntimeError("send outcome unknown"))
    session.upstream = cast(Any, SimpleNamespace(send_text=send))
    state = proxy._WebSocketRequestState(
        request_id="uncertain-owner",
        model=None,
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0,
    )
    with pytest.raises(RuntimeError, match="send outcome unknown"):
        await _send_http_bridge_request_text_with_archive_id(
            session,
            state,
            '{"type":"response.create","previous_response_id":"resp-original","input":"continue"}',
            clock=clock,
        )
    assert state.response_create_sent_at is None
    assert state.response_create_attempt_count == 1
    await _prompt_refusal(service, owner, scheduler, response_create_attempt_count=state.response_create_attempt_count)
    assert clock.monotonic() == 0
    assert send.await_count == 1
    assert opened.await_count == 0


@pytest.mark.asyncio
async def test_followup_replacement_cannot_start_another_owner_window(monkeypatch: pytest.MonkeyPatch) -> None:
    service, owner, scheduler, clock, opened = _bridge(monkeypatch)
    await _prompt_refusal(service, owner, scheduler, allow_required_owner_recovery=False)
    assert clock.monotonic() == 0
    assert opened.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_excluded_or_untyped_owner_refuses_without_heuristic_wait(
    monkeypatch: pytest.MonkeyPatch, legacy: bool
) -> None:
    service, owner, scheduler, clock, opened = _bridge(monkeypatch)
    if legacy:
        monkeypatch.setattr(
            service._load_balancer,
            "select_account",
            AsyncMock(
                return_value=lb.AccountSelection(None, "Rate limit exceeded. Try again in 2s", "account_stream_cap")
            ),
        )
    refusal = await _prompt_refusal(service, owner, scheduler, exclude_account_ids=None if legacy else {owner.id})
    assert clock.monotonic() == 0
    assert opened.await_count == 0
    assert _http_bridge_capacity_wait_plan(refusal, request_deadline=120, now=0) is None


@pytest.mark.asyncio
async def test_required_owner_mismatch_keeps_outer_stream_anchor_and_releases_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, owner, scheduler, _, opened = _bridge(monkeypatch, blocked="none")
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
    payload = proxy.ResponsesRequest.model_validate(
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
    lookup = proxy.DurableBridgeLookup(
        session_id="mismatched-owner",
        canonical_kind="session_header",
        canonical_key="mismatched-owner",
        api_key_scope="__anonymous__",
        account_id=owner.id,
        owner_instance_id=None,
        owner_epoch=1,
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=60),
        state=HttpBridgeSessionState.ACTIVE,
        latest_turn_state="mismatched-owner",
        latest_response_id=payload.previous_response_id,
        latest_input_item_count=len(historical_input),
        latest_input_full_fingerprint=proxy._fingerprint_input_items(historical_input),
        model=payload.model,
    )
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=lookup))
    monkeypatch.setattr(service._durable_bridge, "lookup_recovery_attempt", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_http_bridge_has_live_local_session", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_http_bridge_can_forward_to_active_owner", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_resolve_file_account_for_responses", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_resolve_websocket_previous_response_owner", AsyncMock(return_value=owner.id))
    instance = proxy.get_settings().http_responses_session_bridge_instance_id
    monkeypatch.setattr(proxy, "_http_bridge_owner_instance", AsyncMock(return_value=instance))
    monkeypatch.setattr(proxy, "_active_http_bridge_instance_ring", AsyncMock(return_value=(instance, (instance,))))
    alternate = _account("mismatched-alternate")
    lease = await service._load_balancer.acquire_account_lease(alternate.id, kind="stream")
    assert lease is not None
    # Force an inconsistent selector result; no natural selector path is known
    # to violate this invariant. Keep real creation, lease release and outer replay.
    select = AsyncMock(return_value=lb.AccountSelection(alternate, None, lease=lease))
    monkeypatch.setattr(service._load_balancer, "select_account", select)
    get_or_create = service._get_or_create_http_bridge_session
    attempts: list[tuple[str | None, str | None, set[str]]] = []
    refusal: ProxyResponseError | None = None

    async def observe_creation(key: Any, **kwargs: Any) -> Any:
        nonlocal refusal
        attempts.append(
            (kwargs["preferred_account_id"], kwargs["previous_response_id"], set(kwargs["exclude_account_ids"] or ()))
        )
        if refusal is not None:
            # Bound a broken outer replay after recording its changed owner and
            # anchor, without allowing the forced selector to dispatch upstream.
            raise refusal
        try:
            return await get_or_create(key, **kwargs)
        except ProxyResponseError as exc:
            refusal = exc
            raise

    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", observe_creation)
    try:
        with pytest.raises(ProxyResponseError) as caught:
            async for _ in service._stream_via_http_bridge(
                payload,
                headers={"x-codex-session-id": "mismatched-owner"},
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
            ):
                pass
        assert attempts == [(owner.id, "resp_required_anchor", set())], "mismatch must not drop owner/anchor or replay"
        assert select.await_count == 1, caught.value.payload
        assert select.await_args is not None
        assert select.await_args.kwargs["required_account_id"] == owner.id
        assert caught.value.status_code == 502
        assert caught.value.payload == {
            "error": {
                "code": "previous_response_owner_unavailable",
                "message": "Previous response owner account is unavailable; retry later.",
                "type": "server_error",
            }
        }
        assert payload.previous_response_id == "resp_required_anchor"
        assert opened.await_count == 0
        assert service._load_balancer._runtime[alternate.id].inflight_streams == 0
        assert not service._load_balancer._runtime[alternate.id].leases
        assert not service._http_bridge_inflight_sessions
        assert not service._http_bridge_sessions
    finally:
        await scheduler.cancel_owned_tasks()
