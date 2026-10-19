"""Subscription silence deadlines at HTTP and WebSocket product boundaries.

All upstreams are inert; virtual time advances hours without a live request.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import aiohttp
import pytest
from fastapi.testclient import TestClient

from app.core.clients import proxy as upstream_proxy
from app.core.clients.codex import CodexClient
from app.core.clients.native_egress import NativeEgressTransportError
from app.core.config.settings import Settings
from app.core.openai.requests import ResponsesRequest
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy import api as proxy_api
from app.modules.proxy import service as proxy_service
from tests.integration.test_proxy_websocket_responses import (
    _FakeUpstreamMessage,
    _FakeUpstreamWebSocket,
    _websocket_settings,
)
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.integration


class _HttpPeer:
    def __init__(self, scheduler: VirtualScheduler, phase: str) -> None:
        self.scheduler = scheduler
        self.phase = phase
        self.calls = 0
        self.closed = False
        self.timeout: aiohttp.ClientTimeout | None = None
        self.status = self.status_code = 200
        self.headers = {"content-type": "text/event-stream"}
        self.content = self

    async def iter_chunked(self, _size: int):
        if self.phase != "accepted":
            yield b'data: {"type":"response.created","response":{"id":"resp_silence"}}\n\n'
        if self.phase == "healthy":
            for _ in range(4):
                await self.scheduler.sleep(500)
                # Transport comments are activity even without a model token.
                yield b": upstream heartbeat\n\n"
            yield (
                b'data: {"type":"response.completed","response":{"id":"resp_silence",'
                b'"status":"completed","output":[]}}\n\n'
            )
        else:
            await asyncio.Event().wait()

    def release(self) -> None:
        self.closed = True

    async def request(self, _method: str, _url: str, **kwargs: Any):
        self.calls += 1
        timeout = kwargs["timeout"]
        self.timeout = timeout if isinstance(timeout, aiohttp.ClientTimeout) else aiohttp.ClientTimeout(total=timeout)
        if self.phase == "headers":
            delay = min(x for x in (self.timeout.total, self.timeout.sock_read) if x is not None)
            try:
                await self.scheduler.sleep(delay)
                raise aiohttp.SocketTimeoutError("inert peer did not send headers")
            finally:
                self.closed = True
        return self

    @asynccontextmanager
    async def post(self, url: str, **kwargs: Any):
        try:
            yield await self.request("POST", url, **kwargs)
        finally:
            self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("routed", [False, True], ids=["direct", "routed"])
@pytest.mark.parametrize(
    ("phase", "native", "total_budget"),
    [
        ("headers", False, 7200),
        ("accepted", False, 7200),
        ("midstream", False, 7200),
        ("healthy", False, 7200),
        ("headers", True, 7200),
        ("headers", True, 300),
    ],
)
async def test_http_route_subscription_silence(
    async_client, monkeypatch, routed: bool, phase: str, native: bool, total_budget: int
) -> None:
    clock = VirtualClock(monotonic_value=1000)
    scheduler = VirtualScheduler(clock)
    peer = _HttpPeer(scheduler, phase)
    settings = Settings(http_responses_stream_request_budget_seconds=total_budget)
    monkeypatch.setattr(upstream_proxy, "get_settings", lambda: settings)
    monkeypatch.setattr(upstream_proxy, "with_dashboard_overrides", lambda value: value)

    async def native_request(request):
        peer.calls += 1
        peer.timeout = aiohttp.ClientTimeout(
            total=request.timeout_seconds,
            sock_connect=request.connect_timeout_seconds,
            sock_read=request.response_head_timeout_seconds,
        )
        delay = min(x for x in (peer.timeout.total, peer.timeout.sock_read) if x is not None)
        try:
            await scheduler.sleep(delay)
            raise NativeEgressTransportError("inert native response head timed out", failure_phase="timeout")
        finally:
            peer.closed = True

    native_client = SimpleNamespace(request=native_request) if native else None
    monkeypatch.setattr(upstream_proxy, "discover_native_egress_client", lambda: native_client)
    monkeypatch.setattr(upstream_proxy, "time", SimpleNamespace(monotonic=clock.monotonic))
    # Replace only the low-level client's clock/wait surface, not global asyncio.
    monkeypatch.setattr(
        upstream_proxy,
        "asyncio",
        SimpleNamespace(
            **{name: getattr(asyncio, name) for name in dir(asyncio) if not name.startswith("__")},
        ),
    )
    monkeypatch.setattr(upstream_proxy.asyncio, "wait", scheduler.wait)
    monkeypatch.setattr(upstream_proxy.asyncio, "create_task", scheduler.create_task)
    route = (
        ResolvedUpstreamRoute(
            endpoint=ResolvedProxyEndpoint(id="inert-proxy", scheme="http", host="proxy.invalid", port=8080),
            mode="pool",
            pool_id="inert-pool",
        )
        if routed
        else None
    )
    codex_client = CodexClient(cast(Any, peer), native_egress_client=cast(Any, native_client)) if routed else None

    async def stream_from_inert_peer(self, payload: ResponsesRequest, headers, **kwargs):
        del self, kwargs
        async for event in upstream_proxy.stream_responses(
            payload,
            headers,
            "inert-token",
            "inert-account",
            session=cast(Any, peer),
            upstream_stream_transport_override="http",
            route=route,
            codex_client=codex_client,
        ):
            yield event

    monkeypatch.setattr(proxy_service.ProxyService, "stream_responses", stream_from_inert_peer)
    monkeypatch.setattr(proxy_service.ProxyService, "stream_http_responses", stream_from_inert_peer)
    request = scheduler.create_task(
        async_client.post(
            "/v1/responses",
            json={
                "model": "gpt-5.4",
                "input": "hello",
                "stream": True,
            },
        )
    )
    try:
        # Allow the real local ASGI/SQLite preflight to reach the inert transport.
        for _ in range(100):
            await scheduler.drain()
            if peer.calls:
                break
            await asyncio.sleep(0.001)
        assert peer.calls == 1
        await scheduler.advance(min(600, total_budget) - 1)
        assert not request.done()
        await scheduler.advance(1)
        if phase == "healthy":
            assert not request.done()
            await scheduler.advance(1400)
        # ASGI startup-probe/heartbeat ownership uses real short local timers.
        response = await asyncio.wait_for(request, timeout=2) if request.done() or phase == "healthy" else None
        assert response is not None, "silent upstream outlived the 600-second phase deadline"
        assert response.status_code == 200
        if phase == "healthy":
            assert "response.completed" in response.text
            assert "stream_idle_timeout" not in response.text
        elif total_budget < 600:
            assert ("upstream_unavailable" if routed else "upstream_error") in response.text
            assert "stream_idle_timeout" not in response.text
        else:
            assert "stream_idle_timeout" in response.text
        assert peer.closed
        assert peer.calls == 1
        assert peer.timeout is not None
        assert peer.timeout.total == pytest.approx(total_budget)
        assert peer.timeout.sock_connect == 8
        assert peer.timeout.sock_read == 600
    finally:
        await scheduler.cancel_owned_tasks()
    assert not scheduler.owned_tasks


@pytest.mark.parametrize("phase", ["accepted", "midstream", "healthy"])
@pytest.mark.parametrize("total_budget", [650, 7200])
def test_websocket_route_subscription_silence(app_instance, monkeypatch, phase: str, total_budget: int) -> None:
    clock = VirtualClock(monotonic_value=1000)
    scheduler = VirtualScheduler(clock)
    runtime_settings = _websocket_settings(
        http_responses_stream_request_budget_seconds=total_budget,
        stream_idle_timeout_seconds=600,
        proxy_downstream_websocket_idle_timeout_seconds=7200,
    )
    service = get_proxy_service_for_app(app_instance)
    service._clock = clock
    service._scheduler = scheduler
    upstream = _FakeUpstreamWebSocket([])
    account = proxy_service.Account(
        id="inert-account",
        chatgpt_account_id="inert-account",
        email="inert@example.invalid",
        plan_type="plus",
        access_token_encrypted=b"inert",
        refresh_token_encrypted=b"inert",
        id_token_encrypted=b"inert",
        last_refresh=proxy_service.utcnow(),
        status=proxy_service.AccountStatus.ACTIVE,
    )
    reservation = proxy_service.ApiKeyUsageReservationData(
        reservation_id="inert-reservation",
        key_id="inert-key",
        model="gpt-5.4",
    )
    states = []

    async def connect_peer(*args, request_state, **kwargs):
        request_state.api_key_reservation = reservation
        states.append(request_state)
        return account, upstream

    connect = AsyncMock(side_effect=connect_peer)
    logs = AsyncMock()
    release = AsyncMock()
    health = AsyncMock()
    monkeypatch.setattr(proxy_api, "_websocket_firewall_denial_response", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy_api, "validate_proxy_api_key_authorization", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy_service, "get_settings", lambda: runtime_settings)
    monkeypatch.setattr(
        proxy_service, "get_settings_cache", lambda: SimpleNamespace(get=AsyncMock(return_value=runtime_settings))
    )
    monkeypatch.setattr(service, "_connect_proxy_websocket", connect)
    monkeypatch.setattr(service, "_write_request_log", logs)
    monkeypatch.setattr(service, "_release_websocket_reservation", release)
    monkeypatch.setattr(service._load_balancer, "record_success", AsyncMock())
    monkeypatch.setattr(service._load_balancer, "record_error", health)
    monkeypatch.setattr(service, "_acquire_account_response_create_lease_or_overload", AsyncMock(return_value=None))

    async def feed(event_type: str) -> None:
        upstream._messages.put_nowait(
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": event_type,
                        "response": {"id": "resp_silence", "status": "in_progress", "output": []},
                    }
                ),
            )
        )
        await scheduler.drain()

    with TestClient(app_instance) as client:
        assert client.portal is not None
        with client.websocket_connect("/backend-api/codex/responses") as downstream:
            downstream.send_json({"type": "response.create", "model": "gpt-5.4", "input": "hello", "stream": True})
            client.portal.call(scheduler.drain)
            if phase != "accepted":
                client.portal.call(feed, "response.created")
                assert downstream.receive_json()["type"] == "response.created"
            client.portal.call(scheduler.advance, 599)
            assert not upstream.closed
            if phase == "healthy":
                # A lifecycle event, with no output token, renews the idle wait.
                client.portal.call(feed, "response.in_progress")
                if total_budget == 650:
                    client.portal.call(scheduler.advance, 51)
                    assert states[0].api_key_reservation is None, "original overall budget was extended"
                else:
                    client.portal.call(scheduler.advance, 599)
                    assert not upstream.closed
                    client.portal.call(feed, "response.completed")
            else:
                client.portal.call(scheduler.advance, 1)
                assert upstream.closed, "local keepalives rearmed the upstream idle deadline"
            events = []
            while True:
                event = downstream.receive_json()
                events.append(event)
                if event["type"] in {"response.failed", "response.completed"}:
                    break
            terminal = events[-1]
            assert terminal["type"] == (
                "response.completed" if phase == "healthy" and total_budget == 7200 else "response.failed"
            )
            if phase != "healthy":
                assert terminal["response"]["error"]["code"] == "stream_idle_timeout"
            if phase == "healthy" and total_budget == 650:
                assert terminal["response"]["error"]["code"] == "upstream_request_timeout"
            if phase != "healthy":
                release.assert_awaited_once_with(reservation)
                assert states[0].api_key_reservation is None
                assert states[0].response_create_gate_acquired is False
            assert connect.await_count == 1
            assert len(upstream.sent_text) == 1
            health.assert_not_awaited()
        client.portal.call(scheduler.drain)
        client.portal.call(scheduler.cancel_owned_tasks)
    assert not scheduler.owned_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True], ids=["pre-response", "midstream"])
async def test_http_bridge_route_silence_uses_phase_deadline(async_client, monkeypatch, started: bool) -> None:
    from tests.unit.test_http_bridge_eventless_semantics import _make_bridge_session

    clock = VirtualClock(monotonic_value=1000)
    scheduler = VirtualScheduler(clock)
    service = get_proxy_service_for_app(async_client._transport.app)
    service._clock, service._scheduler = clock, scheduler
    settings = Settings()
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    detach = AsyncMock()
    reconnect = AsyncMock()
    monkeypatch.setattr(service, "_detach_http_bridge_request", detach)
    monkeypatch.setattr(service, "_reconnect_http_bridge_session", reconnect)
    monkeypatch.setattr(service, "_http_bridge_precreated_retry_cooldown_seconds", AsyncMock(return_value=0))
    service._durable_bridge = cast(
        Any,
        SimpleNamespace(
            lookup_retry_circuit=AsyncMock(return_value=None),
            persist_retry_circuit=AsyncMock(return_value=None),
        ),
    )
    session = _make_bridge_session()
    state = proxy_service._WebSocketRequestState(
        request_id="bridge-silence",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=clock.monotonic(),
        event_queue=asyncio.Queue(),
        transport="http",
        previous_response_id="resp_owner_pin",
        response_id="resp_bridge" if started else None,
        response_event_count=1 if started else 0,
        request_text='{"type":"response.create"}',
    )
    submitted = asyncio.Event()

    async def submit(target_session, *, request_state, **kwargs):
        target_session.pending_requests.append(request_state)
        if started:
            request_state.event_queue.put_nowait(
                'data: {"type":"response.created","response":{"id":"resp_bridge"}}\n\n'
            )
        submitted.set()

    monkeypatch.setattr(service, "_submit_http_bridge_request", submit)

    async def bridge_stream(self, payload, headers, **kwargs):
        async for event in self._stream_http_bridge_session_events(
            session,
            request_state=state,
            text_data=state.request_text,
            queue_limit=8,
            propagate_http_errors=False,
            downstream_turn_state=None,
        ):
            yield event

    monkeypatch.setattr(proxy_service.ProxyService, "stream_http_responses", bridge_stream)
    monkeypatch.setattr(proxy_service.ProxyService, "stream_responses", bridge_stream)
    request = scheduler.create_task(
        async_client.post(
            "/v1/responses",
            json={
                "model": "gpt-5.4",
                "input": "hello",
                "stream": True,
            },
        )
    )
    try:
        await asyncio.wait_for(submitted.wait(), timeout=2)
        await scheduler.drain()
        deadline = 600 if started else 300
        await scheduler.advance(deadline - 1)
        assert not request.done()
        await scheduler.advance(1)
        response = await asyncio.wait_for(request, timeout=2)
        assert response.status_code == 200
        assert ("stream_idle_timeout" if started else "bridge_eventless_timeout") in response.text
        assert state.previous_response_id == "resp_owner_pin"
        reconnect.assert_not_awaited()
        detach.assert_awaited_once()
    finally:
        await scheduler.cancel_owned_tasks()
    assert not scheduler.owned_tasks
