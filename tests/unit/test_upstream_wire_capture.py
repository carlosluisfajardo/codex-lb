from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
import weakref
from collections import deque
from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import aiohttp
import anyio
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.core.clients import codex as codex_client
from app.core.clients import http as http_client
from app.core.clients import native_egress, proxy_websocket
from app.core.clients import proxy as core_proxy
from app.core.clients import upstream_wire_capture as capture
from app.core.clients.http import lease_http_session
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.openai.requests import ResponsesRequest
from app.core.utils.request_id import reset_request_id, reset_request_scope_id, set_request_id, set_request_scope_id
from app.db.models import Account
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge import request_submit
from app.modules.proxy._service.request_log import _RequestLogMixin
from app.modules.proxy._service.support import _WebSocketRequestState, _WebSocketUpstreamControl
from app.modules.proxy._service.websocket import mixin as websocket_mixin
from app.modules.proxy._service.websocket.mixin import _WebSocketMixin

pytestmark = pytest.mark.unit

SENTINEL = "SENTINEL-c4f1e2"
THREAD = "01a10e4d-1104-7bc2-97b4-a7de1c6159cd"


class _TrapStr(str):
    """A str subclass whose methods must never run inside the capture."""

    touched: bool = False

    def __str__(self) -> str:
        type(self).touched = True
        return SENTINEL

    def __repr__(self) -> str:
        type(self).touched = True
        return SENTINEL

    def __eq__(self, other: object) -> bool:
        type(self).touched = True
        return True

    def __hash__(self) -> int:
        type(self).touched = True
        return hash("priority")


class _TrapObject:
    def __str__(self) -> str:
        return SENTINEL

    def __repr__(self) -> str:
        return SENTINEL


class _Upstream:
    """Weakref-able stand-in for ArchivingUpstreamWebSocket."""

    def __init__(self, route_mode: str | None = "direct", *, fail: bool = False) -> None:
        self.upstream_proxy_route_mode = route_mode
        self.sent: list[str] = []
        self._fail = fail

    async def send_text(self, text: str) -> None:
        if self._fail:
            raise ConnectionError("send failed")
        self.sent.append(text)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        return None


@pytest.fixture(autouse=True)
def _isolated_capture(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(get_settings(), "wire_capture_file", None)
    monkeypatch.setattr(capture, "_ACTIVE", None)
    monkeypatch.setattr(capture, "_FAILED", False)
    yield
    active = capture._ACTIVE
    if active is not None:
        current = os.fstat(active.fd)
        if (current.st_dev, current.st_ino) == active.identity:
            os.close(active.fd)


@pytest.fixture
def capture_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    run_dir.chmod(0o700)
    path = run_dir / "wire-capture.jsonl"
    monkeypatch.setattr(get_settings(), "wire_capture_file", path)
    return path


@pytest.fixture
def ids() -> Iterator[tuple[str, str]]:
    request_id, scope_id = str(uuid.uuid4()), str(uuid.uuid4())
    request_token, scope_token = set_request_id(request_id), set_request_scope_id(scope_id)
    try:
        yield request_id, scope_id
    finally:
        reset_request_scope_id(scope_token)
        reset_request_id(request_token)


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _of(path: Path, kind: str) -> list[dict[str, Any]]:
    return [record for record in _records(path) if record["record"] == kind]


@pytest.fixture
async def loopback() -> AsyncIterator[TestServer]:
    async def responses(request: web.Request) -> web.StreamResponse:
        if request.method == "GET":
            if request.headers.get("x-test-reject-upgrade"):
                return web.Response(status=426)
            websocket = web.WebSocketResponse()
            await websocket.prepare(request)
            async for message in websocket:
                if message.type == aiohttp.WSMsgType.TEXT:
                    await websocket.send_str(
                        json.dumps(
                            {
                                "type": "response.completed",
                                "response": {"id": "resp_ws1", "status": "completed", "service_tier": "default"},
                            }
                        )
                    )
                    break
            await websocket.close()
            return websocket
        await request.read()
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(
            b'data: {"type":"response.created","response":{"id":"resp_http1","status":"in_progress"}}\n\n'
            b'data: {"type":"response.completed","response":{"id":"resp_http1","status":"completed",'
            b'"service_tier":"default"}}\n\n'
        )
        await response.write_eof()
        return response

    async def redirect(request: web.Request) -> web.Response:
        await request.read()
        raise web.HTTPTemporaryRedirect("/backend-api/v2/codex/responses")

    async def reject_upgrade(request: web.Request) -> web.Response:
        return web.Response(status=426)

    app = web.Application()
    app.router.add_route("*", "/backend-api/codex/responses", responses)
    app.router.add_route("*", "/backend-api/v2/codex/responses", responses)
    app.router.add_post("/backend-api/redirect/codex/responses", redirect)
    app.router.add_get("/reject/backend-api/codex/responses", reject_upgrade)
    app.router.add_post("/reject/backend-api/codex/responses", responses)
    app.router.add_post("/other", responses)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    try:
        yield server
    finally:
        await server.close()


def _traced_session(label: str = "shared") -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=5),
        trust_env=False,
        trace_configs=capture.trace_configs(session_label=label),
    )


def _payload(tier: object = "priority") -> ResponsesRequest:
    return ResponsesRequest.model_validate(
        {"model": "gpt-5", "instructions": "", "input": "hello", "service_tier": tier, "stream": True}
    )


# --- default-off and sink custody -------------------------------------------------------------


def test_disabled_capture_allocates_nothing(tmp_path: Path) -> None:
    assert capture.capture_enabled() is False
    assert capture.trace_configs(session_label="shared") is None
    assert capture.snapshot_bridge_send(upstream=_Upstream(), pending_requests=deque()) is None
    capture.record_outgoing_payload(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        method="POST",
        url="https://x/codex/responses",
        payload={"service_tier": "priority"},
    )
    capture.record_request_log_row(
        archive_request_id=None,
        request_id="resp_a",
        conversation_id=None,
        status="success",
        request_kind="normal",
        transport="http",
        upstream_transport=None,
        upstream_proxy_route_mode=None,
        requested_service_tier=None,
        actual_service_tier=None,
        upstream_status_code=None,
    )
    assert capture._ACTIVE is None
    assert list(tmp_path.iterdir()) == []


async def test_enabled_sink_is_private_new_file_with_startup_open_record(capture_path: Path) -> None:
    configs = capture.trace_configs(session_label="shared")
    second = capture.trace_configs(session_label="shared")

    assert configs is not None and len(configs) == 1
    assert second is not None and second[0] is not configs[0]
    async with aiohttp.ClientSession(trace_configs=configs), aiohttp.ClientSession(trace_configs=second):
        pass
    assert capture_path.stat().st_mode & 0o777 == 0o600
    records = _records(capture_path)
    assert [record["record"] for record in records] == ["capture_open"]
    assert records[0]["schema"] == capture.WIRE_CAPTURE_SCHEMA
    assert records[0]["process_id"] == os.getpid()
    assert records[0]["seq"] == 0


@pytest.mark.parametrize("case", ["relative", "existing_file", "symlink", "open_parent", "symlinked_parent"])
def test_unsafe_sink_paths_keep_capture_disabled(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    run_dir.chmod(0o700)
    target = run_dir / "wire-capture.jsonl"
    if case == "relative":
        target = Path("wire-capture.jsonl")
    elif case == "existing_file":
        target.write_text("")
    elif case == "symlink":
        (run_dir / "elsewhere").write_text("")
        target.symlink_to(run_dir / "elsewhere")
    elif case == "open_parent":
        run_dir.chmod(0o770)
    elif case == "symlinked_parent":
        link = tmp_path / "link"
        link.symlink_to(run_dir, target_is_directory=True)
        target = link / "wire-capture.jsonl"
    monkeypatch.setattr(get_settings(), "wire_capture_file", target)

    assert capture.trace_configs(session_label="shared") is None
    assert capture.capture_enabled() is False
    if case in {"existing_file", "symlink"}:
        assert (run_dir / ("wire-capture.jsonl" if case == "existing_file" else "elsewhere")).read_text() == ""
    else:
        assert not (run_dir / "wire-capture.jsonl").exists()


async def test_capture_failure_disables_capture_without_changing_request(
    capture_path: Path, loopback: TestServer, ids: tuple[str, str]
) -> None:
    async with _traced_session() as session:
        assert capture._ACTIVE is not None
        read_only = os.open(capture_path, os.O_RDONLY)
        os.close(capture._ACTIVE.fd)
        capture._ACTIVE.fd = read_only
        async with session.post(loopback.make_url("/backend-api/codex/responses"), json={}) as response:
            body = await response.read()
        async with session.post(loopback.make_url("/backend-api/codex/responses"), json={}) as again:
            assert again.status == 200

    assert response.status == 200
    assert b"resp_http1" in body
    assert capture._FAILED is True
    assert capture.capture_enabled() is False
    assert [record["record"] for record in _records(capture_path)] == ["capture_open"]
    with pytest.raises(OSError):
        os.fstat(read_only)


def test_reused_sink_descriptor_is_never_written_or_closed(capture_path: Path, tmp_path: Path) -> None:
    assert capture.trace_configs(session_label="shared") is not None
    assert capture._ACTIVE is not None
    sink_fd = capture._ACTIVE.fd
    other = tmp_path / "other"
    other_fd = os.open(other, os.O_WRONLY | os.O_CREAT, 0o600)
    os.dup2(other_fd, sink_fd)
    os.close(other_fd)
    try:
        capture.record_request_log_row(
            archive_request_id=None,
            request_id="resp_a",
            conversation_id=None,
            status="success",
            request_kind="normal",
            transport="http",
            upstream_transport=None,
            upstream_proxy_route_mode=None,
            requested_service_tier=None,
            actual_service_tier=None,
            upstream_status_code=None,
        )
        assert other.read_bytes() == b""
        assert os.fstat(sink_fd).st_ino == other.stat().st_ino
        assert capture.capture_enabled() is False
    finally:
        os.close(sink_fd)


def test_reused_sink_descriptor_survives_record_limit_failure(
    capture_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert capture.trace_configs(session_label="shared") is not None
    assert capture._ACTIVE is not None
    sink_fd = capture._ACTIVE.fd
    monkeypatch.setattr(capture, "_MAX_RECORDS", capture._ACTIVE.seq)
    other = tmp_path / "foreign-resource"
    other_fd = os.open(other, os.O_WRONLY | os.O_CREAT, 0o600)
    os.dup2(other_fd, sink_fd)
    os.close(other_fd)
    try:
        capture.record_outgoing_payload(
            url="https://example.invalid/backend-api/codex/responses",
            payload={"service_tier": "priority"},
            direction="codex_to_server",
            kind="responses",
            transport="http",
            method="POST",
        )
        assert other.read_bytes() == b""
        assert os.fstat(sink_fd).st_ino == other.stat().st_ino
        assert capture.capture_enabled() is False
    finally:
        try:
            os.close(sink_fd)
        except OSError:
            pass


def test_live_connection_tracking_is_bounded_without_losing_existing_references(
    capture_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capture, "_MAX_TRACKED", 2)
    upstreams = [_Upstream() for _ in range(3)]
    snapshots = [capture.snapshot_bridge_send(upstream=upstream, pending_requests=deque()) for upstream in upstreams]
    assert capture._ACTIVE is not None
    assert len(capture._ACTIVE.connections) == 2
    assert snapshots[0] is not None and snapshots[0].connection is not None
    assert snapshots[2] is not None and snapshots[2].connection is None
    again = capture.snapshot_bridge_send(upstream=upstreams[0], pending_requests=deque())
    assert again is not None and again.connection is snapshots[0].connection
    del upstreams[1]
    admitted = capture.snapshot_bridge_send(upstream=upstreams[1], pending_requests=deque())
    assert admitted is not None and admitted.connection is not None and admitted.connection.ref == 3


def test_record_count_and_note_memory_are_bounded(capture_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture, "_MAX_RECORDS", 3)
    monkeypatch.setattr(capture, "_MAX_TRACKED", 2)
    for index in range(3):
        capture.note_bridge_frame_tier(
            bridge_request_ref=f"ws_{index:032x}", outgoing_service_tier="priority", transport="http"
        )
    assert capture._ACTIVE is not None
    assert list(capture._ACTIVE.notes) == [f"ws_{1:032x}", f"ws_{2:032x}"]
    for _ in range(5):
        capture.record_request_log_row(
            archive_request_id=None,
            request_id="resp_a",
            conversation_id=None,
            status="success",
            request_kind="normal",
            transport="http",
            upstream_transport=None,
            upstream_proxy_route_mode=None,
            requested_service_tier=None,
            actual_service_tier=None,
            upstream_status_code=None,
        )
    assert len(_records(capture_path)) == 3
    assert capture.capture_enabled() is False


# --- value validation -------------------------------------------------------------------------


def test_request_log_row_rejects_sentinels_inside_allowed_fields(capture_path: Path) -> None:
    _TrapStr.touched = False
    capture.record_request_log_row(
        archive_request_id=f"{SENTINEL} 4c1a2f0e-7d39-4e5b-9a61-0f3b2c8d9e10",
        request_id=f"resp_{SENTINEL}",
        conversation_id=_TrapStr(THREAD),
        status=_TrapStr("success"),
        request_kind={"normal": SENTINEL},
        transport=[SENTINEL],
        upstream_transport=_TrapObject(),
        upstream_proxy_route_mode=SENTINEL,
        requested_service_tier=f"priority {SENTINEL}",
        actual_service_tier=_TrapStr("default"),
        upstream_status_code=True,
    )

    text = capture_path.read_text()
    assert SENTINEL not in text
    assert _TrapStr.touched is False
    row = _of(capture_path, "request_log_row")[0]
    assert row["ingress_request_id"] is None and row["ingress_request_id_state"] == "noncanonical"
    assert row["request_log_response_id"] is None and row["request_log_response_id_format"] == "noncanonical"
    assert row["conversation_id"] is None and row["conversation_id_state"] == "noncanonical"
    for field in ("status", "request_kind", "transport", "upstream_transport_configured", "route_mode"):
        assert row[field] is None and row[f"{field}_state"] == "unrecognized"
    assert row["requested_service_tier"] is None and row["requested_service_tier_state"] == "unrecognized"
    assert row["actual_service_tier"] is None and row["actual_service_tier_state"] == "unrecognized"
    assert row["upstream_status_code"] is None and row["upstream_status_code_state"] == "unrecognized"


def test_request_log_row_keeps_allowed_values_and_unresolved_id_origin(capture_path: Path) -> None:
    capture.record_request_log_row(
        archive_request_id="4c1a2f0e-7d39-4e5b-9a61-0f3b2c8d9e10",
        request_id="resp_68af0123abc",
        conversation_id=THREAD,
        status="success",
        request_kind="normal",
        transport="http",
        upstream_transport="websocket",
        upstream_proxy_route_mode="direct",
        requested_service_tier="priority",
        actual_service_tier=None,
        upstream_status_code=502,
    )

    row = _of(capture_path, "request_log_row")[0]
    assert row["request_log_response_id"] == "resp_68af0123abc"
    assert row["request_log_response_id_format"] == "resp_format"
    assert row["request_log_response_id_origin"] == "request_log_row_request_id_caller_dependent"
    assert row["requested_service_tier"] == "priority"
    assert row["actual_service_tier"] is None and row["actual_service_tier_state"] == "not_observed"
    assert row["upstream_status_code"] == 502
    assert row["upstream_status_code_origin"] == "failure_metadata_event_or_mapped_not_wire"


def test_outgoing_payload_never_writes_host_query_or_unknown_tiers(capture_path: Path, ids: tuple[str, str]) -> None:
    capture.record_outgoing_payload(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        method="POST",
        url=f"https://{SENTINEL}.example:8443/backend-api/codex/responses?token={SENTINEL}",
        payload={"service_tier": SENTINEL, "input": SENTINEL},
    )
    capture.record_outgoing_payload(
        direction="codex_to_server",
        kind="responses",
        transport="websocket",
        method="GET",
        url="wss://h/backend-api/codex/responses",
        payload=cast(Any, _TrapObject()),
    )
    capture.record_outgoing_payload(
        direction="server_to_codex",
        kind="responses",
        transport="http",
        method="POST",
        url="https://h/backend-api/codex/responses",
        payload={"service_tier": "priority"},
    )
    capture.record_outgoing_payload(
        direction="codex_to_server",
        kind="responses",
        transport="http",
        method="POST",
        url="https://h/backend-api/files",
        payload={"service_tier": "priority"},
    )

    assert SENTINEL not in capture_path.read_text()
    first, second = _of(capture_path, "outgoing_payload")
    assert first["path_class"] == "codex_responses" and first["scheme"] == "https"
    assert first["send_state"] == "pre_send"
    assert first["outgoing_service_tier"] is None and first["outgoing_service_tier_state"] == "unrecognized"
    assert second["outgoing_service_tier_state"] == "not_observed"


async def test_trace_records_final_status_redirect_count_and_hint_tier(
    capture_path: Path, loopback: TestServer, ids: tuple[str, str]
) -> None:
    request_id, scope_id = ids
    async with _traced_session() as session:
        async with session.post(
            loopback.make_url("/backend-api/redirect/codex/responses"),
            json={},
            headers={"x-codex-routing-hint": "model=gpt-5;tier=priority", "authorization": f"Bearer {SENTINEL}"},
        ) as response:
            await response.read()
        async with session.post(
            loopback.make_url(f"/backend-api/codex/responses?q={SENTINEL}"),
            json={},
            headers={"x-codex-routing-hint": f"model={SENTINEL};tier={SENTINEL}"},
        ) as response:
            await response.read()
        async with session.post(loopback.make_url("/other"), json={}) as response:
            await response.read()

    assert SENTINEL not in capture_path.read_text()
    redirected, sentinel_hint = _of(capture_path, "upstream_http_hop")
    assert redirected["method"] == "POST" and redirected["status"] == 200
    assert redirected["status_origin"] == "aiohttp_final_response_status"
    assert redirected["redirects_followed"] == 1
    assert redirected["routing_hint_tier"] == "priority"
    assert redirected["http_version"] == "1.1"
    assert redirected["ingress_request_id"] == request_id and redirected["scope_id"] == scope_id
    assert redirected["session_label"] == "shared"
    assert sentinel_hint["routing_hint_tier"] is None and sentinel_hint["routing_hint_tier_state"] == "unrecognized"
    assert sentinel_hint["hop_ordinal_in_scope"] == 2


# --- call-site wiring and held-code contracts --------------------------------------------------


async def test_shared_http_client_carries_trace_configs_only_when_enabled(
    capture_path: Path, loopback: TestServer, monkeypatch: pytest.MonkeyPatch, ids: tuple[str, str]
) -> None:
    monkeypatch.setattr(get_settings(), "wire_capture_file", None)
    disabled = await http_client._build_http_client()
    try:
        assert disabled.session.trace_configs == []
        assert capture._ACTIVE is None
    finally:
        await http_client._close_client(disabled)
    monkeypatch.setattr(get_settings(), "wire_capture_file", capture_path)
    enabled = await http_client._build_http_client()
    try:
        async with enabled.session.post(loopback.make_url("/backend-api/codex/responses"), json={}) as response:
            await response.read()
    finally:
        await http_client._close_client(enabled)

    (hop,) = _of(capture_path, "upstream_http_hop")
    assert hop["session_label"] == "shared" and hop["status"] == 200


async def test_routed_and_socks_sessions_carry_trace_configs(
    capture_path: Path, loopback: TestServer, monkeypatch: pytest.MonkeyPatch, ids: tuple[str, str]
) -> None:
    routed = codex_client.create_codex_session()
    try:
        async with routed.post(loopback.make_url("/backend-api/codex/responses"), json={}) as response:
            await response.read()
    finally:
        await routed.close()
    monkeypatch.setattr(codex_client, "_socks_proxy_connector", lambda _endpoint: aiohttp.TCPConnector())
    await codex_client._request_via_socks_proxy(
        "POST",
        str(loopback.make_url("/backend-api/codex/responses")),
        cast(Any, SimpleNamespace(id="endpoint")),
        buffer_response=True,
        json={},
    )

    labels = [hop["session_label"] for hop in _of(capture_path, "upstream_http_hop")]
    assert labels == ["routed", "routed_socks"]


async def test_core_http_stream_records_pre_send_payload_then_post_hop(
    capture_path: Path, loopback: TestServer, monkeypatch: pytest.MonkeyPatch, ids: tuple[str, str]
) -> None:
    request_id, scope_id = ids
    monkeypatch.setattr(core_proxy, "discover_native_egress_client", lambda: None)
    async with _traced_session() as session:
        events = [
            event
            async for event in core_proxy.stream_responses(
                _payload(),
                {},
                "fixture-token",
                None,
                base_url=str(loopback.make_url("/backend-api")),
                session=session,
                upstream_stream_transport_override="http",
                synthesize_routing_hint=True,
            )
        ]

    assert events
    records = [record for record in _records(capture_path) if record["record"] != "capture_open"]
    assert [record["record"] for record in records] == ["outgoing_payload", "upstream_http_hop"]
    payload, hop = records
    assert payload["transport"] == "http" and payload["method"] == "POST"
    assert payload["outgoing_service_tier"] == "priority" and payload["send_state"] == "pre_send"
    assert payload["origin"] == "archive_json_send_object_field"
    assert hop["method"] == "POST" and hop["status"] == 200 and hop["routing_hint_tier"] == "priority"
    assert {payload["ingress_request_id"], hop["ingress_request_id"]} == {request_id}
    assert {payload["scope_id"], hop["scope_id"]} == {scope_id}


async def test_core_websocket_stream_records_upgrade_and_pre_send_frame(
    capture_path: Path, loopback: TestServer, ids: tuple[str, str]
) -> None:
    async with _traced_session() as session:
        events = [
            event
            async for event in core_proxy.stream_responses(
                _payload(),
                {},
                "fixture-token",
                None,
                base_url=str(loopback.make_url("/backend-api")),
                session=session,
                upstream_stream_transport_override="websocket",
            )
        ]

    assert events
    records = [record for record in _records(capture_path) if record["record"] != "capture_open"]
    assert [record["record"] for record in records] == ["upstream_http_hop", "outgoing_payload"]
    upgrade, frame = records
    assert upgrade["method"] == "GET" and upgrade["status"] == 101
    assert frame["transport"] == "websocket" and frame["send_state"] == "pre_send"
    assert frame["outgoing_service_tier"] == "priority"


async def test_core_rejected_upgrade_falls_back_to_recorded_http_post(
    capture_path: Path, loopback: TestServer, monkeypatch: pytest.MonkeyPatch, ids: tuple[str, str]
) -> None:
    monkeypatch.setattr(core_proxy, "discover_native_egress_client", lambda: None)
    async with _traced_session() as session:
        events = [
            event
            async for event in core_proxy.stream_responses(
                _payload(),
                {"originator": "codex_cli_rs"},
                "fixture-token",
                None,
                base_url=str(loopback.make_url("/reject/backend-api")),
                session=session,
                upstream_stream_transport_override="auto",
            )
        ]

    assert events
    records = [record for record in _records(capture_path) if record["record"] != "capture_open"]
    shape = [
        (record["record"], record.get("method"), record.get("status"), record.get("transport")) for record in records
    ]
    assert shape == [
        ("upstream_http_hop", "GET", 426, None),
        ("outgoing_payload", "POST", None, "http"),
        ("upstream_http_hop", "POST", 200, None),
    ]


async def test_direct_websocket_open_contract_used_by_bridge_send(
    capture_path: Path, loopback: TestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    native_egress.discover_native_egress_client.cache_clear()
    monkeypatch.setattr(native_egress.shutil, "which", lambda _name: None)
    try:
        upstream = await proxy_websocket.connect_responses_websocket(
            {}, "fixture-token", None, base_url=str(loopback.make_url("/backend-api")), allow_direct_egress=True
        )
        try:
            assert getattr(upstream, "upstream_proxy_route_mode", None) == "direct"
            assert weakref.ref(upstream)() is upstream
            assert native_egress.discover_native_egress_client.cache_info().currsize == 1
            assert capture._native_discovery_state() == "absent"
        finally:
            await upstream.close()
    finally:
        native_egress.discover_native_egress_client.cache_clear()


# --- HTTP bridge ------------------------------------------------------------------------------


def _bridge_session(upstream: object, pending: deque[_WebSocketRequestState]) -> proxy_service._HTTPBridgeSession:
    return proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-capture", None),
        headers={},
        affinity=proxy_service._AffinityPolicy(key="sid-capture", kind=proxy_service.StickySessionKind.CODEX_SESSION),
        request_model="gpt-5",
        account=cast(Any, SimpleNamespace(id="acc-capture")),
        upstream=cast(Any, upstream),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=pending,
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=len(pending),
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )


def _prepared_bridge_request(ingress: str, tier: object = "priority") -> tuple[_WebSocketRequestState, str]:
    service = proxy_service.ProxyService(cast(Any, SimpleNamespace()))
    request_state, text = service._prepare_response_bridge_request_state(
        _payload(tier),
        api_key=None,
        api_key_reservation=None,
        include_type_field=True,
        attach_event_queue=False,
        transport="http",
        client_metadata=None,
        headers={},
        request_log_id=ingress,
    )
    request_state.conversation_id = THREAD
    return request_state, text


async def test_bridge_first_send_records_prepared_tier_connection_and_contract_status(
    capture_path: Path, monkeypatch: pytest.MonkeyPatch, ids: tuple[str, str]
) -> None:
    request_id, scope_id = ids
    monkeypatch.setattr(capture, "_native_discovery_state", lambda: "absent")
    request_state, text = _prepared_bridge_request(request_id)
    upstream = _Upstream("direct")
    session = _bridge_session(upstream, deque([request_state]))

    await request_submit._send_http_bridge_request_text_with_archive_id(session, request_state, text)
    await request_submit._send_http_bridge_request_text_with_archive_id(session, request_state, text)
    failing = _Upstream("direct", fail=True)
    session.upstream = cast(Any, failing)
    with pytest.raises(ConnectionError):
        await request_submit._send_http_bridge_request_text_with_archive_id(session, request_state, text)

    first, retry = _of(capture_path, "bridge_frame_sent")
    assert first["bridge_request_ref"] == request_state.request_id
    assert first["ingress_request_id"] == request_id and first["scope_id"] == scope_id
    assert first["conversation_id"] == THREAD
    assert first["attempt_ordinal"] == 1 and first["pending_request_count"] == 1
    assert first["send_state"] == "local_send_completed"
    assert first["outgoing_service_tier"] == "priority"
    assert first["outgoing_service_tier_origin"] == "prepared_response_create_dict_field_first_send"
    assert first["connection_ref"] == 1 and first["connection_first_send"] is True
    assert first["handshake_status"] == 101
    assert first["handshake_status_origin"] == "websockets_library_contract_101"
    assert first["opener_hint_tier"] is None and first["opener_hint_tier_state"] == "not_observed"
    assert first["opener_hint_tier_origin"] is None
    assert retry["attempt_ordinal"] == 2 and retry["connection_ref"] == 1
    assert retry["connection_first_send"] is False
    assert retry["outgoing_service_tier"] is None and retry["outgoing_service_tier_state"] == "not_observed"
    assert retry["handshake_status"] is None and retry["opener_hint_tier_state"] == "not_observed"


@pytest.mark.parametrize(
    ("route_mode", "native", "expected_origin"),
    [
        ("direct", "present", "unknown_native_possible"),
        ("direct", "unknown", "unknown_native_possible"),
        ("default_pool", "absent", "unknown_routed_unmatched"),
        (SENTINEL, "absent", "unknown_route_mode"),
    ],
)
async def test_bridge_handshake_status_is_only_claimed_for_direct_websockets(
    capture_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ids: tuple[str, str],
    route_mode: str,
    native: str,
    expected_origin: str,
) -> None:
    monkeypatch.setattr(capture, "_native_discovery_state", lambda: native)
    request_state, text = _prepared_bridge_request(ids[0])
    session = _bridge_session(_Upstream(route_mode), deque([request_state]))

    await request_submit._send_http_bridge_request_text_with_archive_id(session, request_state, text)

    (sent,) = _of(capture_path, "bridge_frame_sent")
    assert sent["handshake_status"] is None and sent["handshake_status_origin"] == expected_origin
    assert SENTINEL not in capture_path.read_text()


async def test_bridge_routed_status_comes_from_same_scope_routed_upgrade_trace(
    capture_path: Path, loopback: TestServer, monkeypatch: pytest.MonkeyPatch, ids: tuple[str, str]
) -> None:
    monkeypatch.setattr(capture, "_native_discovery_state", lambda: "absent")
    routed = codex_client.create_codex_session()
    try:
        async with routed.ws_connect(loopback.make_url("/backend-api/codex/responses")):
            pass
    finally:
        await routed.close()
    request_state, text = _prepared_bridge_request(ids[0])
    session = _bridge_session(_Upstream("default_pool"), deque([request_state]))

    await request_submit._send_http_bridge_request_text_with_archive_id(session, request_state, text)

    (sent,) = _of(capture_path, "bridge_frame_sent")
    assert sent["handshake_status"] == 101
    assert sent["handshake_status_origin"] == "aiohttp_routed_upgrade_same_scope"


async def test_bridge_unregistrable_receiver_and_prewarm_without_note_stay_unknown(
    capture_path: Path, monkeypatch: pytest.MonkeyPatch, ids: tuple[str, str]
) -> None:
    monkeypatch.setattr(capture, "_native_discovery_state", lambda: "absent")
    sent_texts: list[str] = []

    async def send_text(text: str) -> None:
        sent_texts.append(text)

    request_state, text = _prepared_bridge_request(ids[0])
    namespace_upstream = SimpleNamespace(send_text=send_text, upstream_proxy_route_mode="direct")
    session = _bridge_session(namespace_upstream, deque([request_state]))
    await request_submit._send_http_bridge_request_text_with_archive_id(session, request_state, text)
    prewarm = _WebSocketRequestState(
        request_id=f"http_prewarm_{uuid.uuid4().hex}",
        model="gpt-5",
        service_tier="priority",
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        transport="http",
    )
    session.upstream = cast(Any, _Upstream("direct"))
    await request_submit._send_http_bridge_request_text_with_archive_id(session, prewarm, '{"type":"response.create"}')

    unregistered, prewarm_sent = _of(capture_path, "bridge_frame_sent")
    assert unregistered["connection_ref"] is None and unregistered["connection_ref_state"] == "unregistrable"
    assert unregistered["handshake_status"] is None
    assert prewarm_sent["outgoing_service_tier"] is None
    assert prewarm_sent["outgoing_service_tier_state"] == "not_observed"
    assert prewarm_sent["conversation_id"] is None


@pytest.mark.parametrize("replay_alias", [None, "resp_alias123"])
async def test_bridge_detach_records_upstream_response_id_and_replay_alias(
    capture_path: Path, monkeypatch: pytest.MonkeyPatch, ids: tuple[str, str], replay_alias: str | None
) -> None:
    service = proxy_service.ProxyService(cast(Any, SimpleNamespace()))
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())
    request_state, _ = _prepared_bridge_request(ids[0])
    request_state.response_id = "resp_upstream123"
    request_state.replay_downstream_response_id = replay_alias
    request_state.actual_service_tier = "default"
    request_state.response_create_attempt_count = 1
    session = _bridge_session(_Upstream(), deque([request_state]))

    await service._detach_http_bridge_request(session, request_state=request_state)

    (settled,) = _of(capture_path, "bridge_request_settled")
    assert settled["bridge_request_ref"] == request_state.request_id
    assert settled["ingress_request_id"] == ids[0] and settled["conversation_id"] == THREAD
    assert settled["request_kind"] == "normal" and settled["attempt_count"] == 1
    assert settled["upstream_response_id"] == "resp_upstream123"
    assert settled["upstream_response_id_origin"] == "bridge_request_state_response_id_at_detach"
    assert settled["replay_alias_present"] is (replay_alias is not None)
    assert settled["actual_service_tier"] == "default"
    assert capture._ACTIVE is not None and request_state.request_id not in capture._ACTIVE.notes


# --- Q4 actual-tier handoff through real service seams ---------------------------------------


async def test_non_bridge_stream_once_hands_actual_tier_to_request_log_capture(
    capture_path: Path, loopback: TestServer, monkeypatch: pytest.MonkeyPatch, ids: tuple[str, str]
) -> None:
    request_id, _ = ids
    session = _traced_session()

    def local_lease(
        existing: aiohttp.ClientSession | None = None,
    ) -> AbstractAsyncContextManager[aiohttp.ClientSession]:
        return lease_http_session(existing or session)

    monkeypatch.setattr(core_proxy, "lease_http_session", local_lease)
    monkeypatch.setattr(core_proxy, "discover_native_egress_client", lambda: None)
    monkeypatch.setattr(get_settings(), "upstream_base_url", str(loopback.make_url("/backend-api")))
    service = proxy_service.ProxyService(cast(Any, SimpleNamespace()))
    account = Account(
        id="capture-account",
        chatgpt_account_id=None,
        access_token_encrypted=service._encryptor.encrypt("fixture-subscription-token"),
    )
    monkeypatch.setattr(service, "_resolve_upstream_route_for_account", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_persist_request_log", AsyncMock())
    try:
        events = [
            event
            async for event in service._stream_once(
                account,
                _payload(),
                {},
                request_id,
                False,
                request_started_at=service._clock.monotonic(),
                api_key=None,
                api_key_reservation=None,
                settlement=proxy_service._StreamSettlement(),
                suppress_text_done_events=False,
                upstream_stream_transport="http",
                request_transport="http",
                conversation_id=THREAD,
            )
        ]
    finally:
        await session.close()

    assert events
    (row,) = _of(capture_path, "request_log_row")
    assert row["ingress_request_id"] == request_id and row["conversation_id"] == THREAD
    assert row["requested_service_tier"] == "priority" and row["actual_service_tier"] == "default"
    assert row["request_log_response_id"] == "resp_http1"
    assert row["request_log_response_id_origin"] == "request_log_row_request_id_caller_dependent"
    verdict = capture.classify_capture(_records(capture_path), conversation_id=THREAD)
    assert verdict["path"] == "direct_http"
    assert verdict["tier_verdict"] == "UNKNOWN"
    assert verdict["completeness"] == "PARTIAL"
    assert verdict["reasons"] == ["upstream_response_id_unresolved"]
    assert verdict["status"] == 200 and verdict["status_origin"] == "aiohttp_final_response_status"


class _BridgeLogService(_WebSocketMixin, _RequestLogMixin):
    def __init__(self) -> None:
        self._background_cleanup_tasks: set[asyncio.Task[None]] = set()
        self._encryptor = TokenEncryptor()
        self.persisted: list[dict[str, object]] = []

        class _LoadBalancer:
            async def record_success(self, _account: object) -> None:
                return None

        self._load_balancer = _LoadBalancer()

    async def _persist_request_log(self, **kwargs: object) -> None:
        self.persisted.append(kwargs)

    def _track_request_log_task(self, task: asyncio.Task[None], **_kwargs: object) -> None:
        return None

    def _cancel_request_state_api_key_reservation_heartbeat(self, _request_state: _WebSocketRequestState) -> None:
        return None

    async def _settle_stream_api_key_usage(self, *_args: object, **_kwargs: object) -> bool:
        return True

    async def _release_websocket_request_state_reservation(self, _request_state: _WebSocketRequestState) -> None:
        return None

    def _remember_websocket_previous_response_owner(self, **_kwargs: object) -> None:
        return None


async def test_bridge_finalizer_hands_actual_tier_and_alias_id_to_request_log_capture(
    capture_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_op_release_gate(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(websocket_mixin, "_release_websocket_response_create_gate", no_op_release_gate)
    ingress = str(uuid.uuid4())
    request_state = _WebSocketRequestState(
        request_id=f"ws_{uuid.uuid4().hex}",
        request_log_id=ingress,
        archive_request_id=ingress,
        response_id="resp_upstream456",
        model="gpt-5",
        service_tier="default",
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        transport="http",
        upstream_transport="websocket",
        requested_service_tier="priority",
        actual_service_tier="default",
    )
    request_state.conversation_id = THREAD
    request_state.replay_downstream_response_id = "resp_alias456"

    await _BridgeLogService()._finalize_websocket_request_state(
        request_state,
        account=cast(Any, object()),
        account_id_value="acc_bridge",
        event=None,
        event_type="response.completed",
        payload={},
        api_key=None,
        upstream_control=_WebSocketUpstreamControl(),
        response_create_gate=asyncio.Semaphore(1),
    )

    (row,) = _of(capture_path, "request_log_row")
    assert row["ingress_request_id"] == ingress and row["conversation_id"] == THREAD
    assert row["actual_service_tier"] == "default" and row["requested_service_tier"] == "priority"
    assert row["request_log_response_id"] == "resp_alias456"
    assert row["request_log_response_id_origin"] == "request_log_row_request_id_caller_dependent"


@pytest.mark.parametrize("raw_tier", [pytest.param(..., id="missing"), None, 42, "", {}])
async def test_bridge_finalizer_normalized_none_never_proves_upstream_tier_omission(
    capture_path: Path, monkeypatch: pytest.MonkeyPatch, ids: tuple[str, str], raw_tier: Any
) -> None:
    async def no_op_release_gate(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(websocket_mixin, "_release_websocket_response_create_gate", no_op_release_gate)
    monkeypatch.setattr(capture, "_native_discovery_state", lambda: "absent")
    request_state, text = _prepared_bridge_request(ids[0])
    request_state.response_id = "resp_rawtier42"
    session = _bridge_session(_Upstream(), deque([request_state]))
    await request_submit._send_http_bridge_request_text_with_archive_id(session, request_state, text)
    await _BridgeLogService()._finalize_websocket_request_state(
        request_state,
        account=cast(Any, object()),
        account_id_value="acc_fixture",
        event=None,
        event_type="response.completed",
        payload={"response": {} if raw_tier is ... else {"service_tier": raw_tier}},
        api_key=None,
        upstream_control=_WebSocketUpstreamControl(),
        response_create_gate=asyncio.Semaphore(1),
    )
    capture.record_bridge_request_settled(request_state)

    (row,) = _of(capture_path, "request_log_row")
    (settled,) = _of(capture_path, "bridge_request_settled")
    assert row["actual_service_tier_state"] == settled["actual_service_tier_state"] == "not_observed"
    verdict = capture.classify_capture(_records(capture_path), conversation_id=THREAD)
    assert verdict["tier_verdict"] == "UNKNOWN" and verdict["completeness"] == "PARTIAL"
    assert "returned_tier_not_observed" in verdict["reasons"]


# --- conservative classification -----------------------------------------------------------


def _bridge_chain(**overrides: Any) -> list[dict[str, Any]]:
    ingress, ref = "4c1a2f0e-7d39-4e5b-9a61-0f3b2c8d9e10", "ws_" + "a" * 32
    sent = {
        "record": "bridge_frame_sent",
        "send_state": "local_send_completed",
        "bridge_request_ref": ref,
        "ingress_request_id": ingress,
        "conversation_id": THREAD,
        "attempt_ordinal": 1,
        "pending_request_count": 1,
        "connection_ref": 1,
        "connection_ref_state": "registered",
        "connection_first_send": True,
        "handshake_status": 101,
        "handshake_status_origin": "websockets_library_contract_101",
        "outgoing_service_tier": "priority",
        "outgoing_service_tier_state": "present",
        "opener_hint_tier": None,
    }
    settled = {
        "record": "bridge_request_settled",
        "bridge_request_ref": ref,
        "ingress_request_id": ingress,
        "conversation_id": THREAD,
        "request_kind": "normal",
        "attempt_count": 1,
        "upstream_response_id": "resp_up1",
        "upstream_response_id_state": "present",
        "replay_alias_present": False,
        "actual_service_tier": "default",
        "actual_service_tier_state": "present",
    }
    row = {
        "record": "request_log_row",
        "ingress_request_id": ingress,
        "conversation_id": THREAD,
        "request_kind": "normal",
        "status": "success",
        "actual_service_tier": "default",
        "actual_service_tier_state": "present",
    }
    for key, value in overrides.items():
        target, field = key.split("__")
        {"sent": sent, "settled": settled, "row": row}[target][field] = value
    return [{"record": "capture_open"}, sent, settled, row]


def test_classifier_reports_complete_bridge_chain() -> None:
    verdict = capture.classify_capture(_bridge_chain(), conversation_id=THREAD)

    assert verdict["completeness"] == "COMPLETE" and verdict["reasons"] == []
    assert verdict["path"] == "http_bridge"
    assert verdict["tier_verdict"] == "PRIORITY_SENT_DEFAULT_REPORTED"
    assert verdict["upstream_response_id"] == "resp_up1"
    assert verdict["status"] == 101 and verdict["status_origin"] == "websockets_library_contract_101"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (
            {"sent__outgoing_service_tier": None, "sent__outgoing_service_tier_state": "not_observed"},
            "outgoing_tier_not_observed",
        ),
        ({"settled__attempt_count": 2}, "bridge_retry_or_send_count"),
        ({"sent__pending_request_count": 2}, "positional_response_match_ambiguous"),
        (
            {"sent__handshake_status": None, "sent__handshake_status_origin": "unknown_native_possible"},
            "handshake_status_unknown",
        ),
        ({"settled__replay_alias_present": True}, "replay_alias_present"),
        ({"row__status": "error"}, "request_not_successful"),
        ({"sent__ingress_request_id": str(uuid.uuid4())}, "bridge_send_identity_mismatch"),
        ({"sent__conversation_id": str(uuid.uuid4())}, "bridge_send_identity_mismatch"),
        ({"sent__send_state": "pre_send"}, "bridge_local_send_unproven"),
        (
            {"settled__upstream_response_id": None, "settled__upstream_response_id_state": "not_observed"},
            "upstream_response_id_unresolved",
        ),
        (
            {"settled__actual_service_tier": None, "settled__actual_service_tier_state": "unrecognized"},
            "returned_tier_unrecognized",
        ),
        (
            {
                "sent__outgoing_service_tier": "default",
                "settled__actual_service_tier": None,
                "settled__actual_service_tier_state": "unrecognized",
            },
            "returned_tier_unrecognized",
        ),
    ],
)
def test_classifier_withholds_tier_verdict_on_bridge_ambiguity(overrides: dict[str, Any], reason: str) -> None:
    verdict = capture.classify_capture(_bridge_chain(**overrides), conversation_id=THREAD)

    assert verdict["tier_verdict"] == "UNKNOWN"
    assert verdict["completeness"] == "PARTIAL"
    assert reason in verdict["reasons"]


def test_classifier_rejects_another_eligible_row_even_when_one_row_matches_the_bridge() -> None:
    chain = _bridge_chain()
    chain.append({**chain[3], "ingress_request_id": str(uuid.uuid4())})
    verdict = capture.classify_capture(chain, conversation_id=THREAD)
    assert verdict["tier_verdict"] == "UNKNOWN" and verdict["completeness"] == "PARTIAL"
    assert "multiple_eligible_attempts" in verdict["reasons"]


def test_classifier_does_not_prove_fifo_identity_with_another_conversation_on_the_connection() -> None:
    chain = _bridge_chain()
    other_send = {
        **chain[1],
        "conversation_id": str(uuid.uuid4()),
        "ingress_request_id": str(uuid.uuid4()),
        "bridge_request_ref": "ws_" + "b" * 32,
        "connection_first_send": False,
        "pending_request_count": 2,
    }
    chain.insert(2, other_send)
    verdict = capture.classify_capture(chain, conversation_id=THREAD)
    assert verdict["tier_verdict"] == "UNKNOWN" and verdict["completeness"] == "PARTIAL"
    assert "connection_response_mapping_unresolved" in verdict["reasons"]


def test_classifier_partial_cases_never_claim_not_sent_or_omitted() -> None:
    two_rows = _bridge_chain()
    two_rows.append(dict(two_rows[3]))
    no_open = _bridge_chain()[1:]
    cases = [
        capture.classify_capture(no_open, conversation_id=THREAD),
        capture.classify_capture(_bridge_chain(), conversation_id=SENTINEL),
        capture.classify_capture([{"record": "capture_open"}, two_rows[3], dict(two_rows[3])], conversation_id=THREAD),
        capture.classify_capture(
            [
                {"record": "capture_open"},
                {
                    "record": "outgoing_payload",
                    "ingress_request_id": "4c1a2f0e-7d39-4e5b-9a61-0f3b2c8d9e10",
                    "transport": "websocket",
                    "outgoing_service_tier": "priority",
                    "outgoing_service_tier_state": "present",
                },
                two_rows[3],
            ],
            conversation_id=THREAD,
        ),
    ]

    for verdict in cases:
        assert verdict["tier_verdict"] == "UNKNOWN" and verdict["completeness"] == "PARTIAL"
    assert "capture_open_missing" in cases[0]["reasons"]
    assert "invalid_conversation_id" in cases[1]["reasons"]
    assert "multiple_eligible_attempts" in cases[2]["reasons"]
    assert "raw_websocket_frame_send_unproven" in cases[3]["reasons"]


def test_classifier_reports_not_sent_and_omitted_only_with_complete_prerequisites() -> None:
    not_sent = capture.classify_capture(
        _bridge_chain(sent__outgoing_service_tier=None, sent__outgoing_service_tier_state="absent"),
        conversation_id=THREAD,
    )
    omitted = capture.classify_capture(
        _bridge_chain(settled__actual_service_tier=None, settled__actual_service_tier_state="absent"),
        conversation_id=THREAD,
    )

    assert not_sent["tier_verdict"] == "PRIORITY_NOT_SENT" and not_sent["completeness"] == "COMPLETE"
    assert omitted["tier_verdict"] == "PRIORITY_SENT_TIER_OMITTED" and omitted["completeness"] == "COMPLETE"
