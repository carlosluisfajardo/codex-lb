from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest

from app.dependencies import get_proxy_service_for_app
from app.modules.proxy._service.http_bridge import streaming
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions as _cleanup_http_bridge_sessions,
)
from tests.integration.test_http_responses_bridge import (
    _ClosingBridgeUpstreamWebSocket,
    _collect_sse_events,
    _FakeBridgeUpstreamWebSocket,
    _get_account,
    _import_account,
    _install_bridge_settings,
)
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["cooldown", "error_backoff", "capacity"])
@pytest.mark.parametrize("client_anchor", [False, True])
async def test_real_http_owner_recovery_preserves_guarded_replay_and_capacity(
    async_client, app_instance, monkeypatch, blocked, client_anchor
) -> None:
    _install_bridge_settings(monkeypatch, enabled=True)
    owner = await _import_account(async_client, "compat-owner", "compat-owner@example.com")
    service = get_proxy_service_for_app(app_instance)
    first_upstream = _ClosingBridgeUpstreamWebSocket("resp_compat_owner")
    second_upstream = _FakeBridgeUpstreamWebSocket("resp_compat_next")
    opened: list[str] = []

    async def open_upstream(account, _headers, **_kwargs):
        opened.append(account.id)
        return first_upstream if len(opened) == 1 else second_upstream

    monkeypatch.setattr(service, "_open_upstream_websocket_with_budget", open_upstream)
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", AsyncMock(side_effect=lambda account, **_: account))
    historical = [{"role": "user", "content": [{"type": "input_text", "text": "first question"}]}]
    body = {"model": "gpt-5.1", "instructions": "Return OK", "input": historical, "stream": True}
    headers = {"session_id": "owner-compat"}
    first_events = await _collect_sse_events(
        async_client, "/backend-api/codex/responses", json_body=body, headers=headers
    )
    response = first_events[-1]["response"]
    assert opened == [owner]
    alternate = await _import_account(async_client, "compat-alternate", "compat-alternate@example.com")
    clock = VirtualClock(monotonic_value=time.monotonic(), epoch_value=time.time())
    scheduler = VirtualScheduler(clock)
    service._clock, service._scheduler, service._load_balancer._clock = clock, scheduler, clock
    monkeypatch.setattr(time, "time", clock.time)
    runtime = service._load_balancer._runtime[owner]
    if blocked == "cooldown":
        runtime.cooldown_until = clock.time() + 120
    elif blocked == "error_backoff":
        owner_account = await _get_account(owner)
        for _ in range(4):
            await service._load_balancer.record_error(owner_account)
    else:
        runtime.inflight_streams = 100
    full_history = [*historical, response["output"][0], {"role": "user", "content": "next question"}]
    body = dict(body, input=full_history)
    if client_anchor:
        body["previous_response_id"] = response["id"]
    capacity_waits = asyncio.Queue()
    real_capacity_wait = streaming._iter_account_capacity_wait_sse

    async def observe_capacity_wait(**kwargs):
        capacity_waits.put_nowait(kwargs["sleep_seconds"])
        async for line in real_capacity_wait(**kwargs):
            yield line

    monkeypatch.setattr(streaming, "_iter_account_capacity_wait_sse", observe_capacity_wait)
    if blocked == "capacity":
        body["input"] = "continue"
    selected = asyncio.Event()
    real_select = service._load_balancer.select_account
    decisions = []

    async def observe_selection(**kwargs):
        result = await real_select(**kwargs)
        decisions.append((clock.monotonic(), result))
        selected.set()
        return result

    monkeypatch.setattr(service._load_balancer, "select_account", observe_selection)
    started = clock.monotonic()
    task = scheduler.create_task(
        _collect_sse_events(async_client, "/backend-api/codex/responses", json_body=body, headers=headers)
    )
    try:
        await asyncio.wait_for(selected.wait(), 5)
        await scheduler.drain()
        if blocked == "capacity":
            for _ in range(100):
                if not capacity_waits.empty() or task.done():
                    break
                await asyncio.sleep(0.01)
            assert not capacity_waits.empty(), "positive local contention must reach the outer capacity wait"
            assert capacity_waits.get_nowait() == 30
            await scheduler.advance(30)
            assert await asyncio.wait_for(capacity_waits.get(), 5) == 30
            await scheduler.drain()
            await scheduler.advance(15)
            assert not task.done(), "local capacity must wait beyond thirty seconds on its original request budget"
            runtime.inflight_streams = 0
            await scheduler.advance(15)
        else:
            # Drain real SQLite-backed fixture I/O without advancing virtual time.
            for _ in range(100):
                await scheduler.drain()
                if task.done():
                    break
                await asyncio.sleep(0.01)
            assert task.done(), "positive existing full-history proof must replay immediately, before owner waiting"
        events = await asyncio.wait_for(task, 5)
        assert events[-1]["type"] == "response.completed"
        assert opened == [owner, owner if blocked == "capacity" else alternate]
        assert len(second_upstream.sent_text) == 1
        sent = json.loads(second_upstream.sent_text[0])
        if blocked == "capacity":
            assert clock.monotonic() - started == 60
            assert sent.get("previous_response_id") == response["id"]
            assert len(decisions) == 3
        else:
            assert clock.monotonic() == started
            assert "previous_response_id" not in sent
            assert sent["input"] == full_history
            assert len(decisions) == 2
    finally:
        await service.close_all_http_bridge_sessions()
        await scheduler.cancel_owned_tasks()
