from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update

from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy import load_balancer as lb
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions as _cleanup_http_bridge_sessions,
)
from tests.integration.test_http_responses_bridge import (
    _ClosingBridgeUpstreamWebSocket,
    _FakeBridgeUpstreamWebSocket,
    _import_account,
    _install_bridge_settings,
)
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["cooldown", "quota", "rate_limit"])
async def test_http_required_owner_uses_real_selector_and_preserves_anchor(
    async_client, app_instance, monkeypatch: pytest.MonkeyPatch, blocked: str
) -> None:
    _install_bridge_settings(monkeypatch, enabled=True)
    owner_id = await _import_account(async_client, "typed-http-owner", "typed-owner@example.com")
    service = get_proxy_service_for_app(app_instance)
    first_upstream = _ClosingBridgeUpstreamWebSocket()
    second_upstream = _FakeBridgeUpstreamWebSocket("resp_recovered")
    opened: list[str] = []

    async def open_upstream(account, _headers, **_kwargs):
        opened.append(account.id)
        return first_upstream if len(opened) == 1 else second_upstream

    monkeypatch.setattr(service, "_open_upstream_websocket_with_budget", open_upstream)
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", AsyncMock(side_effect=lambda account, **_: account))
    first = await async_client.post("/v1/responses", json={"model": "gpt-5.1", "input": "hello"})
    assert first.status_code == 200
    anchor = first.json()["id"]
    await _import_account(async_client, "typed-http-alternate", "typed-alternate@example.com")
    if blocked != "cooldown":
        async with SessionLocal() as session:
            await session.execute(
                update(Account)
                .where(Account.id == owner_id)
                .values(
                    status=AccountStatus.QUOTA_EXCEEDED if blocked == "quota" else AccountStatus.RATE_LIMITED,
                )
            )
            await session.commit()
        service._load_balancer._selection_inputs_cache.invalidate()

    clock = VirtualClock(monotonic_value=time.monotonic(), epoch_value=time.time())
    scheduler = VirtualScheduler(clock)
    service._clock, service._scheduler, service._load_balancer._clock = clock, scheduler, clock
    monkeypatch.setattr(time, "time", clock.time)
    runtime = service._load_balancer._runtime[owner_id]
    runtime.cooldown_until = clock.time() + 4
    # Terminal states also have local contention: neither may earn an outer wait.
    if blocked != "cooldown":
        runtime.inflight_streams = 100
    selected = asyncio.Event()
    real_select = service._load_balancer.select_account
    decisions: list[lb.AccountSelection] = []

    async def observe_selection(*args, **kwargs):
        result = await real_select(*args, **kwargs)
        decisions.append(result)
        selected.set()
        return result

    monkeypatch.setattr(service._load_balancer, "select_account", observe_selection)
    request = scheduler.create_task(
        async_client.post(
            "/v1/responses",
            json={
                "model": "gpt-5.1",
                "input": "continue",
                "previous_response_id": anchor,
            },
        )
    )
    try:
        await asyncio.wait_for(selected.wait(), 5)
        await scheduler.drain()
        assert decisions[0].account is None
        assert decisions[0].owner_failure_reason == blocked
        if blocked == "cooldown":
            assert not request.done()
            await scheduler.advance(4)
            response = await asyncio.wait_for(request, 5)
            assert response.status_code == 200
            assert opened == [owner_id, owner_id]
            assert len(second_upstream.sent_text) == 1
            assert json.loads(second_upstream.sent_text[0])["previous_response_id"] == anchor
        else:
            response = await asyncio.wait_for(request, 5)
            assert response.status_code == 429
            assert response.json()["error"]["code"] == "account_stream_cap"
            assert opened == [owner_id]
            assert len(decisions) == 1
            assert second_upstream.sent_text == []
    finally:
        await service.close_all_http_bridge_sessions()
        await scheduler.cancel_owned_tasks()
