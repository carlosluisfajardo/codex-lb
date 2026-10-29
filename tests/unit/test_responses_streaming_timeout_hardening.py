from __future__ import annotations

import asyncio
import gc
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast

import pytest

from app.core.clients import proxy as proxy_module
from app.core.clients.proxy import ProxyResponseError
from app.core.config.settings import Settings
from app.core.openai.requests import ResponsesRequest
from app.core.resilience.overload import local_overload_error
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute
from app.core.utils.sse import SSE_KEEPALIVE_FRAME, parse_sse_data_json
from app.modules.proxy import api as proxy_api
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit


def _virtual_first_item_task(scheduler: VirtualScheduler, *, delay: float) -> asyncio.Task[str]:
    """A probe task whose first item arrives after ``delay`` virtual seconds."""

    async def first_item() -> str:
        await scheduler.sleep(delay)
        return "response.created"

    return scheduler.create_task(first_item())


async def _one_event_stream() -> AsyncIterator[str]:
    yield 'event: response.created\ndata: {"type":"response.created"}\n\n'


async def _delayed_429_stream(scheduler: VirtualScheduler) -> AsyncIterator[str]:
    # The first item only resolves after the startup probe has already timed
    # out, then the upstream raises a 429 -- mirroring the response-create
    # admission gate denying admission after the probe window elapsed.
    await scheduler.sleep(0.05)
    raise ProxyResponseError(429, local_overload_error("admission gate timed out", code="global_admission_timeout"))
    yield ""  # pragma: no cover - present only so this is an async generator


@pytest.mark.asyncio
async def test_initial_sse_heartbeat_precedes_openai_contract_event() -> None:
    stream = proxy_api._prepend_initial_sse_heartbeat(
        _one_event_stream(),
        SSE_KEEPALIVE_FRAME,
        request_id="req_test",
        route_family="responses",
    )

    first = await anext(stream)
    second = await anext(stream)

    assert first == SSE_KEEPALIVE_FRAME
    assert "response.created" in second


@pytest.mark.asyncio
async def test_startup_probe_timeout_then_upstream_error_is_not_logged() -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    loop = asyncio.get_running_loop()
    captured: list[str] = []
    loop.set_exception_handler(lambda _loop, context: captured.append(str(context.get("message", ""))))
    try:
        # The probe times out before the first item arrives, so it hands the
        # still-running task to the streamed response.
        probe_task = scheduler.create_task(
            proxy_api._probe_stream_startup_error(
                _delayed_429_stream(scheduler),
                timeout_seconds=0.01,
                scheduler=scheduler,
            )
        )
        await scheduler.drain()
        await scheduler.advance(0.01)
        stream, startup_error = await probe_task
        assert startup_error is None

        # Consuming the handed-off stream surfaces the upstream 429 to the
        # caller -- and must not also emit an "exception in shielded future"
        # diagnostic from the timed-out probe task.
        with pytest.raises(ProxyResponseError):
            await scheduler.advance(0.04)
            async for _ in stream:
                pass

        await scheduler.drain()
        gc.collect()
        await scheduler.drain()
    finally:
        loop.set_exception_handler(None)

    assert not any("shielded future" in m for m in captured), captured


@pytest.mark.asyncio
async def test_abandoned_startup_probe_task_does_not_warn() -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    loop = asyncio.get_running_loop()
    captured: list[str] = []
    loop.set_exception_handler(lambda _loop, context: captured.append(str(context.get("message", ""))))
    try:
        probe_task = scheduler.create_task(
            proxy_api._probe_stream_startup_error(
                _delayed_429_stream(scheduler),
                timeout_seconds=0.01,
                scheduler=scheduler,
            )
        )
        await scheduler.drain()
        await scheduler.advance(0.01)
        stream, startup_error = await probe_task
        assert startup_error is None

        # Drop the wrapping generator without iterating it, as happens when the
        # request is torn down while still waiting on the admission gate. The
        # detached probe task then finishes with its 429 and must not log an
        # "exception was never retrieved" warning when it is collected.
        del stream
        await scheduler.advance(0.1)
        gc.collect()
        await scheduler.drain()
    finally:
        loop.set_exception_handler(None)

    leaked = [m for m in captured if "never retrieved" in m.lower() or "shielded future" in m]
    assert not leaked, f"probe task leaked an unretrieved exception: {captured}"


@pytest.mark.asyncio
async def test_capacity_ready_probe_timeout_uses_virtual_scheduler() -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    first_task = _virtual_first_item_task(scheduler, delay=1.0)
    capacity_ready_event = proxy_api._CapacityStartupReadyEvent(clock=clock)
    capacity_ready_event.set()
    assert capacity_ready_event.set_at == clock.monotonic()
    probe_task = scheduler.create_task(
        proxy_api._wait_for_first_stream_probe(
            first_task,
            timeout_seconds=0.05,
            capacity_wait_event=asyncio.Event(),
            capacity_ready_event=capacity_ready_event,
            scheduler=scheduler,
            clock=clock,
        )
    )

    await scheduler.drain()
    assert probe_task.done() is False
    await scheduler.advance(0.05)

    assert await probe_task is False
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("routed", [False, True], ids=["direct", "routed"])
@pytest.mark.parametrize("activity", ["partial", "complete", "idle", "idle_tie", "cancel", "transport_error"])
async def test_http_sse_timeout_attribution_uses_received_bytes(
    monkeypatch: pytest.MonkeyPatch, activity: str, routed: bool
) -> None:
    """The real parser distinguishes transport silence from an active total expiry."""
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    received_at: list[float] = []
    cleanup: list[str] = []
    calls: list[dict] = []
    completions: list[dict] = []

    class Body:
        async def read_delay(self, delay: float) -> None:
            # Each routed read is bounded by the remaining ORIGINAL total;
            # the scope ends before yielding a chunk to another reader task.
            if routed:
                await scheduler.wait_for(scheduler.sleep(delay), timeout=7200 - clock.monotonic())
            else:
                await scheduler.sleep(delay)

        async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
            try:
                if activity in {"idle", "cancel", "transport_error"}:
                    received_at.append(clock.monotonic())
                    yield b'data: {"type":"response.created","padding":"'
                    if activity == "transport_error":
                        await self.read_delay(300)
                        raise proxy_module.aiohttp.ClientPayloadError("fixture truncated response")
                    await self.read_delay(10_000)
                else:
                    for index in range(22 if activity == "idle_tie" else 23):
                        await self.read_delay(300)
                        received_at.append(clock.monotonic())
                        if activity == "complete":
                            yield b'data: {"type":"response.in_progress","response":{"id":"resp_active"}}\n\n'
                        elif index == 0:
                            yield b'data: {"type":"response.created","padding":"'
                        else:
                            yield b"x"
                    await self.read_delay(10_000)
            finally:
                cleanup.append("body")

    class Session:
        @asynccontextmanager
        async def post(self, url: str, **kwargs):
            calls.append({"url": url, **kwargs})
            timeout = kwargs["timeout"]
            assert timeout.total == 7200
            assert timeout.sock_read == 600
            try:
                with scheduler.fail_after(timeout.total):
                    yield SimpleNamespace(status=200, headers={}, content=Body())
            finally:
                cleanup.append("response")

    class RoutedClient:
        async def request(self, method: str, url: str, **kwargs):
            assert method == "POST"
            assert kwargs["timeout"].total == 7200
            assert kwargs["timeout"].sock_read == 600
            calls.append({"url": url, **kwargs})
            return SimpleNamespace(status=200, headers={}, content=Body(), release=lambda: cleanup.append("response"))

    settings = Settings(
        upstream_base_url="https://upstream.invalid/backend-api",
        stream_idle_timeout_seconds=600,
        http_responses_stream_request_budget_seconds=7200,
        circuit_breaker_enabled=False,
    )
    monkeypatch.setattr(proxy_module, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_module, "time", SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(proxy_module, "asyncio", SimpleNamespace(**{**vars(asyncio), "wait": scheduler.wait}))
    monkeypatch.setattr(proxy_module, "discover_native_egress_client", lambda: None)
    monkeypatch.setattr(proxy_module, "get_request_id", lambda: "req_byte_attribution")
    monkeypatch.setattr(proxy_module, "archive_json", lambda **_kwargs: None)
    monkeypatch.setattr(proxy_module, "archive_text", lambda **_kwargs: None)
    monkeypatch.setattr(proxy_module, "_maybe_log_upstream_request_start", lambda **_kwargs: None)
    monkeypatch.setattr(
        proxy_module, "_maybe_log_upstream_request_complete", lambda **kwargs: completions.append(kwargs)
    )

    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "fixture", "input": "hello", "stream": True}
    )

    async def collect() -> list[str]:
        return [
            event
            async for event in proxy_module.stream_responses(
                payload,
                headers={"x-request-id": "req_byte_attribution"},
                access_token="fixture-token",
                account_id="fixture-account",
                session=cast(proxy_module.aiohttp.ClientSession, Session()),
                route=(
                    ResolvedUpstreamRoute(
                        "proxy", "fixture-pool", ResolvedProxyEndpoint("fixture-endpoint", "http", "proxy.invalid", 80)
                    )
                    if routed
                    else None
                ),
                codex_client=cast(proxy_module.CodexClient, RoutedClient()) if routed else None,
                upstream_stream_transport_override="http",
                suppress_live_usage=True,
            )
        ]

    task = scheduler.create_task(collect())
    try:
        await scheduler.advance(
            600 if activity == "idle" else 300 if activity in {"cancel", "transport_error"} else 7200
        )
        if activity == "cancel":
            assert not task.done()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cleanup == ["body", "response"]
            assert len(calls) == 1
            return
        assert task.done(), "existing idle/total budget must terminate the real request path"
        events = await task
        terminal = parse_sse_data_json(events[-1])
        assert terminal is not None
        expected = (
            "stream_idle_timeout"
            if activity in {"idle", "idle_tie"}
            else "upstream_unavailable"
            if activity == "transport_error"
            else "upstream_request_timeout"
        )
        response = terminal["response"]
        assert isinstance(response, dict)
        error = response["error"]
        assert isinstance(error, dict)
        assert error["code"] == expected
        assert response["id"] == "req_byte_attribution"
        assert len(calls) == 1
        sent_headers = {name.lower(): value for name, value in calls[0]["headers"].items()}
        assert sent_headers["chatgpt-account-id"] == "fixture-account"
        assert cleanup == ["body", "response"]
        assert completions[-1]["error_code"] == expected
        assert completions[-1]["retryable_same_contract"] is False
        if activity == "transport_error":
            assert completions[-1]["failure_exception_type"] == "ClientPayloadError"
        if activity in {"partial", "complete"}:
            assert received_at[-1] >= 6900
            assert len(events) == (len(received_at) + 1 if activity == "complete" else 1)
    finally:
        await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_capacity_signal_discovery_timeout_uses_virtual_scheduler() -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    first_task = _virtual_first_item_task(scheduler, delay=1.0)
    probe_task = scheduler.create_task(
        proxy_api._wait_for_first_stream_probe(
            first_task,
            timeout_seconds=0.01,
            capacity_wait_event=asyncio.Event(),
            scheduler=scheduler,
        )
    )

    await scheduler.drain()
    await scheduler.advance(0.01)
    assert probe_task.done() is False
    await scheduler.advance(0.05)

    assert await probe_task is False
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_capacity_recovery_ready_preserves_first_item_probe() -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    first_task = _virtual_first_item_task(scheduler, delay=0.03)
    capacity_wait_event = asyncio.Event()
    capacity_wait_event.set()
    capacity_ready_event = proxy_api._CapacityStartupReadyEvent(clock=clock)

    async def mark_capacity_ready() -> None:
        await scheduler.sleep(0.02)
        # The producer owns the paired level state: readiness clears the wait
        # marker and sets the ready event (see
        # ``_signal_http_bridge_model_capacity_retry_ready``). The probe only
        # observes both signals.
        capacity_wait_event.clear()
        capacity_ready_event.set()

    scheduler.create_task(mark_capacity_ready())
    probe_task = scheduler.create_task(
        proxy_api._wait_for_first_stream_probe(
            first_task,
            timeout_seconds=0.01,
            capacity_wait_event=capacity_wait_event,
            capacity_ready_event=capacity_ready_event,
            scheduler=scheduler,
            clock=clock,
        )
    )

    await scheduler.drain()
    await scheduler.advance(0.01)
    assert probe_task.done() is False
    await scheduler.advance(0.01)
    assert capacity_ready_event.set_at == clock.monotonic()
    assert capacity_wait_event.is_set() is False
    await scheduler.advance(0.01)

    assert await probe_task is True
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_capacity_recovery_wait_is_unbounded_under_virtual_time() -> None:
    """A set wait marker keeps the probe waiting for the first item indefinitely.

    Main leaves the recovery wait unbounded on purpose: the marker proves the
    request is queued for capacity, so the startup window must not expire it.
    The signal-discovery deadline only bounds the *absence* of a marker.
    """

    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    first_task = _virtual_first_item_task(scheduler, delay=10_000.0)
    capacity_wait_event = asyncio.Event()
    capacity_wait_event.set()
    probe_task = scheduler.create_task(
        proxy_api._wait_for_first_stream_probe(
            first_task,
            timeout_seconds=0.01,
            capacity_wait_event=capacity_wait_event,
            scheduler=scheduler,
            clock=clock,
        )
    )

    await scheduler.drain()
    await scheduler.advance(0.01)
    await scheduler.advance(10 * proxy_api._CAPACITY_WAIT_MARKER_GRACE_SECONDS)
    await scheduler.advance(10 * proxy_api._CAPACITY_STARTUP_SIGNAL_DISCOVERY_SECONDS)

    assert probe_task.done() is False
    assert capacity_wait_event.is_set() is True
    await scheduler.cancel_owned_tasks()
    assert probe_task.cancelled()


@pytest.mark.asyncio
async def test_recovery_ready_rereads_level_state() -> None:
    """Readiness that is immediately superseded by a newer wait keeps the probe waiting.

    The probe never clears the wait marker itself; after the ready signal it
    re-reads the paired level state and, when a newer wait already replaced
    the ready, arms the recovery wait again instead of starting the bounded
    post-ready window or returning.
    """

    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    first_task = _virtual_first_item_task(scheduler, delay=10_000.0)
    capacity_wait_event = asyncio.Event()
    capacity_wait_event.set()
    capacity_ready_event = proxy_api._CapacityStartupReadyEvent(clock=clock)
    probe_task = scheduler.create_task(
        proxy_api._wait_for_first_stream_probe(
            first_task,
            timeout_seconds=0.01,
            capacity_wait_event=capacity_wait_event,
            capacity_ready_event=capacity_ready_event,
            scheduler=scheduler,
            clock=clock,
        )
    )
    await scheduler.drain()
    await scheduler.advance(0.01)
    assert probe_task.done() is False

    # Producer: ready, then a newer wait before the probe task resumes.
    capacity_wait_event.clear()
    capacity_ready_event.set()
    capacity_ready_event.clear()
    capacity_wait_event.set()
    await scheduler.drain()

    assert probe_task.done() is False
    assert capacity_wait_event.is_set() is True
    assert capacity_ready_event.is_set() is False
    await scheduler.advance(10 * proxy_api._CAPACITY_STARTUP_SIGNAL_DISCOVERY_SECONDS)
    assert probe_task.done() is False
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_post_ready_window_rearms_on_newer_wait_marker() -> None:
    """A wait marker raised during the bounded post-ready window keeps holding the headers.

    The resumed upstream can reject again and park the stream on a further
    bounded wait (the owner-bound burst backoff does exactly this up to three
    times). The probe must treat the newer marker like any other wait -- re-read
    the level state and arm the recovery wait -- instead of letting the
    post-ready window expire and hand off a 200/SSE that can no longer carry the
    surfaced HTTP 429.
    """

    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    # 1st redispatch rejected at +0.2 s, second wait 2 s, then the first item.
    first_task = _virtual_first_item_task(scheduler, delay=0.01 + 0.2 + 2.0 + 0.2)
    capacity_wait_event = asyncio.Event()
    capacity_wait_event.set()
    capacity_ready_event = proxy_api._CapacityStartupReadyEvent(clock=clock)
    probe_task = scheduler.create_task(
        proxy_api._wait_for_first_stream_probe(
            first_task,
            timeout_seconds=1.0,
            capacity_wait_event=capacity_wait_event,
            capacity_ready_event=capacity_ready_event,
            scheduler=scheduler,
            clock=clock,
        )
    )
    await scheduler.drain()
    await scheduler.advance(0.01)
    assert probe_task.done() is False

    # Producer: the backoff elapsed and the same account was redispatched.
    capacity_wait_event.clear()
    capacity_ready_event.set()
    await scheduler.drain()
    assert probe_task.done() is False

    # Upstream rejects again 0.2 s later; the stream parks on a 2 s wait that
    # outlives the 1 s post-ready window.
    await scheduler.advance(0.2)
    capacity_ready_event.clear()
    capacity_wait_event.set()
    await scheduler.drain()
    await scheduler.advance(2.0)
    assert probe_task.done() is False
    assert capacity_wait_event.is_set() is True

    # Redispatch, then the first item arrives inside the re-armed window.
    capacity_wait_event.clear()
    capacity_ready_event.set()
    await scheduler.drain()
    await scheduler.advance(0.2)

    assert await probe_task is True
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_post_ready_window_still_expires_without_a_newer_wait_marker() -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    first_task = _virtual_first_item_task(scheduler, delay=10_000.0)
    capacity_wait_event = asyncio.Event()
    capacity_wait_event.set()
    capacity_ready_event = proxy_api._CapacityStartupReadyEvent(clock=clock)
    probe_task = scheduler.create_task(
        proxy_api._wait_for_first_stream_probe(
            first_task,
            timeout_seconds=1.0,
            capacity_wait_event=capacity_wait_event,
            capacity_ready_event=capacity_ready_event,
            scheduler=scheduler,
            clock=clock,
        )
    )
    await scheduler.drain()
    await scheduler.advance(0.01)
    capacity_wait_event.clear()
    capacity_ready_event.set()
    await scheduler.drain()
    assert probe_task.done() is False

    await scheduler.advance(1.0)

    assert await probe_task is False
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_capacity_probe_immediate_completion_keeps_real_scheduler_default() -> None:
    async def first_item() -> str:
        return "response.created"

    first_task = asyncio.create_task(first_item())

    assert (
        await proxy_api._wait_for_first_stream_probe(
            first_task,
            timeout_seconds=0.05,
            capacity_wait_event=asyncio.Event(),
        )
        is True
    )


@pytest.mark.asyncio
async def test_chat_startup_probe_timeout_and_marker_grace_use_virtual_scheduler() -> None:
    clock = VirtualClock()
    scheduler = VirtualScheduler(clock)
    capacity_wait_event = asyncio.Event()

    async def late_first_item() -> AsyncIterator[str]:
        await scheduler.sleep(0.5)
        yield 'data: {"type":"response.created"}\n\n'

    probe = scheduler.create_task(
        proxy_api._probe_chat_stream_startup_error(
            late_first_item(),
            timeout_seconds=0.05,
            capacity_wait_event=capacity_wait_event,
            scheduler=scheduler,
            clock=clock,
        )
    )
    await scheduler.drain()
    assert not probe.done()

    # The startup probe window elapses on the virtual clock ...
    await scheduler.advance(0.05)
    assert not probe.done()
    # ... then the capacity-marker grace window, both without a wall-clock wait.
    await scheduler.advance(proxy_api._CAPACITY_WAIT_MARKER_GRACE_SECONDS)
    stream, startup_error = await probe

    assert startup_error is None
    assert clock.monotonic() == pytest.approx(0.05 + proxy_api._CAPACITY_WAIT_MARKER_GRACE_SECONDS)
    # The first-item probe task is owned by the scheduler and handed to the stream.
    pending_owned = [task for task in scheduler.owned_tasks if not task.done()]
    assert len(pending_owned) == 1

    await scheduler.advance(0.5)
    assert await anext(stream) == 'data: {"type":"response.created"}\n\n'
    await scheduler.cancel_owned_tasks()
