"""Default-off, metadata-only capture of the upstream Responses hop.

Answers, for one labelled request, which service tier left the process on the
actual upstream hop, over which hop, with which status, and which tier the
upstream reported back. Everything is opt-in through the T4 setting
``wire_capture_file``; while it is unset nothing is allocated and HTTP
sessions are built exactly as before.

Every recorded value is validated against a closed set or a canonical id
format. A rejected value is written as ``null`` plus a fixed state and is
never stringified, truncated or hashed. Each status, tier and id carries the
origin that produced it, and ``classify_capture`` only reports a tier verdict
when one attempt is unambiguous (``openspec/changes/add-upstream-wire-capture``).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import stat
import threading
import time
import weakref
from collections import OrderedDict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace
from typing import Final, TypedDict
from urllib.parse import urlsplit

import aiohttp

from app.core.clients import native_egress
from app.core.config.settings import get_settings
from app.core.utils.request_id import get_request_id, get_request_scope_id

WIRE_CAPTURE_SCHEMA: Final = "codex-lb-wire-capture/1"
_MAX_RECORD_BYTES: Final = 2048
_MAX_RECORDS = 100_000
_MAX_TRACKED = 4096

_TIERS: Final = frozenset({"auto", "default", "flex", "scale", "priority", "fast"})
_PRIORITY_TIERS: Final = frozenset({"priority", "fast"})
_REQUEST_KINDS: Final = frozenset({"normal", "compaction", "prewarm", "warmup", "realtime_live"})
_TRANSPORTS: Final = frozenset({"http", "websocket"})
_CONFIGURED_TRANSPORTS: Final = frozenset({"http", "websocket", "auto", "openai_compatible_http"})
_METHODS: Final = frozenset({"GET", "POST"})
_ROUTE_MODES: Final = frozenset({"direct", "account_bound", "default_pool"})
_ROUTED_MODES: Final = frozenset({"account_bound", "default_pool"})
_STATUSES: Final = frozenset({"success", "error", "cancelled"})
_SCHEMES: Final = frozenset({"http", "https", "ws", "wss"})
_SESSION_LABELS: Final = frozenset({"shared", "routed", "routed_socks"})
_HTTP_VERSIONS: Final = {(1, 0): "1.0", (1, 1): "1.1", (2, 0): "2", (3, 0): "3"}

_UUID: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_BRIDGE_REF: Final = re.compile(r"(?:ws|http_prewarm)_[0-9a-f]{32}")
_RESPONSE_ID: Final = re.compile(r"resp_[0-9A-Za-z]{1,128}")
_APP_VERSION: Final = re.compile(r"[0-9][0-9A-Za-z.+-]{0,63}")
_CODEX_RESPONSES_SUFFIX: Final = "/codex/responses"
_ROUTING_HINT_HEADER: Final = "x-codex-routing-hint"

_Value = tuple[str | int | None, str]


@dataclass(slots=True)
class _Connection:
    ref: int
    sends: int = 0


@dataclass(slots=True)
class _ActiveCapture:
    fd: int
    identity: tuple[int, int]
    lock: threading.Lock = field(default_factory=threading.Lock)
    seq: int = 0
    next_connection_ref: int = 1
    notes: OrderedDict[str, _Value] = field(default_factory=OrderedDict)
    scope_hops: OrderedDict[str, int] = field(default_factory=OrderedDict)
    routed_upgrades: OrderedDict[str, int] = field(default_factory=OrderedDict)
    connections: weakref.WeakKeyDictionary[object, _Connection] = field(default_factory=weakref.WeakKeyDictionary)

    def connection_for(self, upstream: object) -> tuple[_Connection | None, str]:
        try:
            existing = self.connections.get(upstream)
            if existing is not None:
                return existing, "registered"
            if len(self.connections) >= _MAX_TRACKED:
                return None, "tracking_limit"
            connection = _Connection(ref=self.next_connection_ref)
            self.connections[upstream] = connection
        except TypeError:
            return None, "unregistrable"
        self.next_connection_ref += 1
        return connection, "registered"


@dataclass(frozen=True, slots=True)
class _SendSnapshot:
    connection: _Connection | None
    connection_state: str
    route_mode: _Value
    pending_request_count: int | None
    scope_id: str | None


_ACTIVE: _ActiveCapture | None = None
_FAILED = False


def _bounded_put[V](mapping: OrderedDict[str, V], key: str, value: V) -> None:
    mapping[key] = value
    mapping.move_to_end(key)
    while len(mapping) > _MAX_TRACKED:
        mapping.popitem(last=False)


def _enum(value: object, allowed: frozenset[str]) -> _Value:
    if value is None:
        return None, "absent"
    if type(value) is str and value in allowed:
        return value, "present"
    return None, "unrecognized"


def _returned_tier(value: object) -> _Value:
    # The service normalizes both absent and invalid event values to None.
    # This seam cannot prove which case occurred at the upstream origin.
    return (None, "not_observed") if value is None else _enum(value, _TIERS)


def _ident(value: object, pattern: re.Pattern[str], limit: int) -> _Value:
    if value is None:
        return None, "absent"
    if type(value) is str and len(value) <= limit and pattern.fullmatch(value):
        return value, "present"
    return None, "noncanonical"


def _status(value: object) -> _Value:
    if value is None:
        return None, "absent"
    if type(value) is int and 100 <= value <= 599:
        return value, "present"
    return None, "unrecognized"


def _count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 1_000_000 else None


def _uuid(value: object) -> _Value:
    return _ident(value, _UUID, 36)


def _uuid_str(value: object) -> str | None:
    canonical = _uuid(value)[0]
    return canonical if isinstance(canonical, str) else None


def _http_version(response: object) -> str | None:
    version = getattr(response, "version", None)
    major, minor = getattr(version, "major", None), getattr(version, "minor", None)
    if type(major) is int and type(minor) is int:
        return _HTTP_VERSIONS.get((major, minor))
    return None


def _put(fields: dict[str, object], name: str, value: _Value) -> None:
    fields[name] = value[0]
    fields[f"{name}_state"] = value[1]


def _url_class(scheme: object, path: object) -> tuple[str | None, str | None]:
    scheme_value = scheme if type(scheme) is str and scheme in _SCHEMES else None
    path_class = (
        "codex_responses" if type(path) is str and len(path) <= 512 and path.endswith(_CODEX_RESPONSES_SUFFIX) else None
    )
    return scheme_value, path_class


def _split_url(url: object) -> tuple[str | None, str | None]:
    if type(url) is not str or len(url) > 4096:
        return None, None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None, None
    return _url_class(parts.scheme, parts.path)


def _hint_tier(value: object) -> _Value:
    if value is None:
        return None, "absent"
    if type(value) is not str or len(value) > 256:
        return None, "unrecognized"
    tiers = [part[len("tier=") :] for part in value.split(";") if part.startswith("tier=")]
    if not tiers:
        return None, "absent"
    if len(tiers) != 1:
        return None, "unrecognized"
    return _enum(tiers[0], _TIERS)


def _exception_category(exc: BaseException) -> str:
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, aiohttp.ClientConnectionError):
        return "connection_error"
    if isinstance(exc, aiohttp.ClientResponseError):
        return "response_error"
    if isinstance(exc, aiohttp.ClientError):
        return "client_error"
    return "other"


def _app_version() -> str | None:
    try:
        version = metadata.version("codex-lb")
    except metadata.PackageNotFoundError:
        return None
    return version if len(version) <= 64 and _APP_VERSION.fullmatch(version) else None


def _open_sink(path: object) -> tuple[int, tuple[int, int]] | None:
    if not isinstance(path, Path) or not path.is_absolute() or path.name in {"", ".", ".."}:
        return None
    try:
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        parent = os.fstat(parent_fd)
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
            return None
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(path.name, flags, 0o600, dir_fd=parent_fd)
    except OSError:
        return None
    finally:
        os.close(parent_fd)
    try:
        created = os.fstat(fd)
        if stat.S_ISREG(created.st_mode) and created.st_uid == os.geteuid() and not created.st_mode & 0o077:
            return fd, (created.st_dev, created.st_ino)
    except OSError:
        pass
    os.close(fd)
    return None


def _fail(*, close: bool = True) -> None:
    global _ACTIVE, _FAILED
    _FAILED = True
    active, _ACTIVE = _ACTIVE, None
    if active is not None and close:
        try:
            current = os.fstat(active.fd)
            if (current.st_dev, current.st_ino) == active.identity:
                os.close(active.fd)
        except OSError:
            pass


def _emit(active: _ActiveCapture, record: str, fields: dict[str, object]) -> None:
    with active.lock:
        if _ACTIVE is not active:
            return
        if active.seq >= _MAX_RECORDS:
            _fail()
            return
        body: dict[str, object] = {
            "schema": WIRE_CAPTURE_SCHEMA,
            "seq": active.seq,
            "at_unix": round(time.time(), 3),
            "record": record,
            **fields,
        }
        line = (json.dumps(body, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")
        if len(line) > _MAX_RECORD_BYTES:
            _fail()
            return
        try:
            current = os.fstat(active.fd)
        except OSError:
            current = None
        if current is None or (current.st_dev, current.st_ino) != active.identity:
            # The descriptor is no longer the sink (closed or reused): never write to or close it.
            _fail(close=False)
            return
        try:
            written = os.write(active.fd, line)
        except OSError:
            written = -1
        if written != len(line):
            _fail()
            return
        active.seq += 1


def _active() -> _ActiveCapture | None:
    global _ACTIVE, _FAILED
    if _FAILED:
        return None
    if _ACTIVE is not None:
        return _ACTIVE
    path = get_settings().wire_capture_file
    if path is None:
        return None
    sink = _open_sink(path)
    if sink is None:
        _FAILED = True
        return None
    active = _ACTIVE = _ActiveCapture(fd=sink[0], identity=sink[1])
    _emit(active, "capture_open", {"process_id": os.getpid(), "app_version": _app_version()})
    return _ACTIVE


def _ids(fields: dict[str, object]) -> str | None:
    _put(fields, "ingress_request_id", _uuid(get_request_id()))
    scope = _uuid(get_request_scope_id())
    _put(fields, "scope_id", scope)
    return _uuid_str(scope[0])


def capture_enabled() -> bool:
    """Whether capture is configured and has not been disabled by a failure."""
    return not _FAILED and get_settings().wire_capture_file is not None


def _record_hop(
    label: str,
    ctx: SimpleNamespace,
    method: object,
    url: object,
    response: object,
    exc: BaseException | None,
) -> None:
    active = _active()
    if active is None:
        return
    scheme, path_class = _url_class(getattr(url, "scheme", None), getattr(url, "path", None))
    if path_class is None:
        return
    fields: dict[str, object] = {}
    scope = _ids(fields)
    ordinal = None
    if scope is not None:
        ordinal = active.scope_hops.get(scope, 0) + 1
        _bounded_put(active.scope_hops, scope, ordinal)
    status = _status(getattr(response, "status", None)) if exc is None else (None, "absent")
    method_value = _enum(method, _METHODS)
    fields.update(
        {
            "session_label": label,
            "hop_ordinal_in_scope": ordinal,
            "method": method_value[0],
            "scheme": scheme,
            "path_class": path_class,
            "http_version": _http_version(response),
            "status_origin": "aiohttp_final_response_status",
            "redirects_followed": _count(getattr(ctx, "capture_redirects", None)),
            "outcome": "response" if exc is None else "exception",
            "exception_category": None if exc is None else _exception_category(exc),
        }
    )
    _put(fields, "status", status)
    _put(fields, "routing_hint_tier", getattr(ctx, "capture_hint", (None, "not_observed")))
    if label != "shared" and method_value[0] == "GET" and status[0] == 101 and scope is not None:
        _bounded_put(active.routed_upgrades, scope, 101)
    _emit(active, "upstream_http_hop", fields)


def _make_trace_config(label: str) -> aiohttp.TraceConfig:
    config = aiohttp.TraceConfig()

    async def on_start(
        _session: aiohttp.ClientSession, ctx: SimpleNamespace, params: aiohttp.TraceRequestStartParams
    ) -> None:
        try:
            ctx.capture_redirects = 0
            _, path_class = _url_class(params.url.scheme, params.url.path)
            if path_class is not None:
                ctx.capture_hint = _hint_tier(params.headers.get(_ROUTING_HINT_HEADER))
        except Exception:
            _fail()

    async def on_redirect(
        _session: aiohttp.ClientSession, ctx: SimpleNamespace, _params: aiohttp.TraceRequestRedirectParams
    ) -> None:
        try:
            ctx.capture_redirects = (_count(getattr(ctx, "capture_redirects", None)) or 0) + 1
        except Exception:
            _fail()

    async def on_end(
        _session: aiohttp.ClientSession, ctx: SimpleNamespace, params: aiohttp.TraceRequestEndParams
    ) -> None:
        try:
            _record_hop(label, ctx, params.method, params.url, params.response, None)
        except Exception:
            _fail()

    async def on_exception(
        _session: aiohttp.ClientSession, ctx: SimpleNamespace, params: aiohttp.TraceRequestExceptionParams
    ) -> None:
        try:
            _record_hop(label, ctx, params.method, params.url, None, params.exception)
        except Exception:
            _fail()

    config.on_request_start.append(on_start)
    config.on_request_redirect.append(on_redirect)
    config.on_request_end.append(on_end)
    config.on_request_exception.append(on_exception)
    return config


def trace_configs(*, session_label: str) -> list[aiohttp.TraceConfig] | None:
    """Return a fresh trace config list for a new session, or ``None`` when capture is off.

    A new ``TraceConfig`` per call: sessions freeze their configs, and the shared
    client is rebuilt on replacement.
    """
    try:
        if _active() is None:
            return None
        label = session_label if type(session_label) is str and session_label in _SESSION_LABELS else "unrecognized"
        return [_make_trace_config(label)]
    except Exception:
        _fail()
        return None


def record_outgoing_payload(
    *, direction: object, kind: object, transport: object, method: object, url: object, payload: object
) -> None:
    """Record the tier of an object the core client is about to send (pre-send only)."""
    try:
        if _FAILED or (_ACTIVE is None and get_settings().wire_capture_file is None):
            return
        if not (
            type(direction) is str and direction == "codex_to_server" and type(kind) is str and kind == "responses"
        ):
            return
        scheme, path_class = _split_url(url)
        if path_class is None:
            return
        active = _active()
        if active is None:
            return
        fields: dict[str, object] = {}
        _ids(fields)
        fields.update(
            {
                "transport": _enum(transport, _TRANSPORTS)[0],
                "method": _enum(method, _METHODS)[0],
                "scheme": scheme,
                "path_class": path_class,
                "send_state": "pre_send",
                "origin": "archive_json_send_object_field",
            }
        )
        tier = _enum(payload.get("service_tier"), _TIERS) if type(payload) is dict else (None, "not_observed")
        _put(fields, "outgoing_service_tier", tier)
        _emit(active, "outgoing_payload", fields)
    except Exception:
        _fail()


def note_bridge_frame_tier(*, bridge_request_ref: object, outgoing_service_tier: object, transport: object) -> None:
    """Remember the tier of a prepared HTTP-bridge ``response.create`` dict (no record)."""
    try:
        if not (type(transport) is str and transport == "http"):
            return
        active = _active()
        if active is None:
            return
        ref = _ident(bridge_request_ref, _BRIDGE_REF, 45)[0]
        if isinstance(ref, str):
            _bounded_put(active.notes, ref, _enum(outgoing_service_tier, _TIERS))
    except Exception:
        _fail()


def snapshot_bridge_send(*, upstream: object, pending_requests: object) -> _SendSnapshot | None:
    """Snapshot the actual send receiver immediately before a bridge frame send."""
    try:
        active = _active()
        if active is None:
            return None
        connection, connection_state = active.connection_for(upstream)
        return _SendSnapshot(
            connection=connection,
            connection_state=connection_state,
            route_mode=_enum(getattr(upstream, "upstream_proxy_route_mode", None), _ROUTE_MODES),
            pending_request_count=_count(len(pending_requests)) if type(pending_requests) is deque else None,
            scope_id=_uuid_str(get_request_scope_id()),
        )
    except Exception:
        _fail()
        return None


def _native_discovery_state() -> str:
    """Native egress discovery from its cached result only (never triggers discovery)."""
    discover = native_egress.discover_native_egress_client
    if discover.cache_info().currsize == 0:
        return "unknown"
    return "absent" if discover() is None else "present"


def _handshake(active: _ActiveCapture, snapshot: _SendSnapshot) -> tuple[int | None, str]:
    mode = snapshot.route_mode[0]
    if mode == "direct":
        if _native_discovery_state() == "absent":
            return 101, "websockets_library_contract_101"
        return None, "unknown_native_possible"
    if mode in _ROUTED_MODES:
        status = active.routed_upgrades.pop(snapshot.scope_id, None) if snapshot.scope_id else None
        if status == 101:
            return 101, "aiohttp_routed_upgrade_same_scope"
        return None, "unknown_routed_unmatched"
    return None, "unknown_route_mode"


def record_bridge_frame_sent(snapshot: _SendSnapshot | None, request_state: object, attempt_ordinal: object) -> None:
    """Record a bridge ``response.create`` frame whose local send completed."""
    if snapshot is None:
        return
    try:
        active = _ACTIVE
        if active is None or _FAILED:
            return
        ref = _ident(getattr(request_state, "request_id", None), _BRIDGE_REF, 45)
        ordinal = _count(attempt_ordinal)
        connection = snapshot.connection
        first_send = connection is not None and connection.sends == 0
        if connection is not None:
            connection.sends += 1
        fields: dict[str, object] = {"send_state": "local_send_completed"}
        _put(fields, "ingress_request_id", _uuid(getattr(request_state, "archive_request_id", None)))
        _put(fields, "scope_id", (snapshot.scope_id, "present" if snapshot.scope_id else "absent"))
        _put(fields, "bridge_request_ref", ref)
        _put(fields, "conversation_id", _uuid(getattr(request_state, "conversation_id", None)))
        note = active.notes.get(ref[0]) if isinstance(ref[0], str) else None
        if note is not None and ordinal == 1:
            _put(fields, "outgoing_service_tier", note)
            fields["outgoing_service_tier_origin"] = "prepared_response_create_dict_field_first_send"
        else:
            _put(fields, "outgoing_service_tier", (None, "not_observed"))
            fields["outgoing_service_tier_origin"] = None
        if first_send:
            handshake = _handshake(active, snapshot)
        else:
            reason = "unknown_not_first_send" if connection is not None else "unknown_unregistered_connection"
            handshake = (None, reason)
        # The first observed frame can follow a different, cancelled opener.
        # Its request state does not establish the original handshake hint.
        _put(fields, "opener_hint_tier", (None, "not_observed"))
        fields["opener_hint_tier_origin"] = None
        route_mode = snapshot.route_mode
        fields.update(
            {
                "attempt_ordinal": ordinal,
                "pending_request_count": snapshot.pending_request_count,
                "connection_ref": connection.ref if connection is not None else None,
                "connection_ref_state": snapshot.connection_state,
                "connection_first_send": first_send,
                "handshake_status": handshake[0],
                "handshake_status_origin": handshake[1],
            }
        )
        _put(fields, "route_mode", route_mode)
        _emit(active, "bridge_frame_sent", fields)
    except Exception:
        _fail()


def record_bridge_request_settled(request_state: object) -> None:
    """Record the bridge request state when its stream detaches (explicit ref -> upstream id)."""
    try:
        active = _active()
        if active is None:
            return
        ref = _ident(getattr(request_state, "request_id", None), _BRIDGE_REF, 45)
        if isinstance(ref[0], str):
            active.notes.pop(ref[0], None)
        fields: dict[str, object] = {}
        _put(fields, "bridge_request_ref", ref)
        _put(fields, "ingress_request_id", _uuid(getattr(request_state, "archive_request_id", None)))
        _put(fields, "conversation_id", _uuid(getattr(request_state, "conversation_id", None)))
        _put(fields, "request_kind", _enum(getattr(request_state, "request_kind", None), _REQUEST_KINDS))
        _put(fields, "upstream_response_id", _ident(getattr(request_state, "response_id", None), _RESPONSE_ID, 133))
        _put(fields, "actual_service_tier", _returned_tier(getattr(request_state, "actual_service_tier", None)))
        fields.update(
            {
                "attempt_count": _count(getattr(request_state, "response_create_attempt_count", None)),
                "upstream_response_id_origin": "bridge_request_state_response_id_at_detach",
                "replay_alias_present": getattr(request_state, "replay_downstream_response_id", None) is not None,
                "actual_service_tier_origin": "upstream_event_response_service_tier_normalized",
            }
        )
        _emit(active, "bridge_request_settled", fields)
    except Exception:
        _fail()


def _response_id_format(value: object) -> _Value:
    if value is None:
        return None, "absent"
    if type(value) is str and len(value) <= 133:
        if _RESPONSE_ID.fullmatch(value):
            return value, "resp_format"
        if _UUID.fullmatch(value):
            return value, "uuid_format"
        if _BRIDGE_REF.fullmatch(value):
            return value, "bridge_ref_format"
    return None, "noncanonical"


def record_request_log_row(
    *,
    archive_request_id: object,
    request_id: object,
    conversation_id: object,
    status: object,
    request_kind: object,
    transport: object,
    upstream_transport: object,
    upstream_proxy_route_mode: object,
    requested_service_tier: object,
    actual_service_tier: object,
    upstream_status_code: object,
) -> None:
    """Record a request-log row as handed to persistence; its id origin stays unresolved."""
    try:
        active = _active()
        if active is None:
            return
        fields: dict[str, object] = {}
        _put(fields, "ingress_request_id", _uuid(archive_request_id))
        _put(fields, "scope_id", _uuid(get_request_scope_id()))
        response_id = _response_id_format(request_id)
        fields["request_log_response_id"] = response_id[0]
        fields["request_log_response_id_format"] = response_id[1]
        fields["request_log_response_id_origin"] = "request_log_row_request_id_caller_dependent"
        _put(fields, "conversation_id", _uuid(conversation_id))
        _put(fields, "status", _enum(status, _STATUSES))
        _put(fields, "request_kind", _enum(request_kind, _REQUEST_KINDS))
        _put(fields, "transport", _enum(transport, _TRANSPORTS))
        _put(fields, "upstream_transport_configured", _enum(upstream_transport, _CONFIGURED_TRANSPORTS))
        _put(fields, "route_mode", _enum(upstream_proxy_route_mode, _ROUTE_MODES))
        _put(fields, "requested_service_tier", _enum(requested_service_tier, _TIERS))
        _put(fields, "actual_service_tier", _returned_tier(actual_service_tier))
        _put(fields, "upstream_status_code", _status(upstream_status_code))
        fields.update(
            {
                "requested_service_tier_origin": "post_policy_payload_attribute",
                "actual_service_tier_origin": "upstream_event_response_service_tier_normalized",
                "upstream_status_code_origin": "failure_metadata_event_or_mapped_not_wire",
            }
        )
        _emit(active, "request_log_row", fields)
    except Exception:
        _fail()


# --- offline classification ---------------------------------------------------------------------

_KNOWN_HANDSHAKE_ORIGINS: Final = frozenset({"websockets_library_contract_101", "aiohttp_routed_upgrade_same_scope"})


class CaptureClassification(TypedDict):
    path: str
    tier_verdict: str
    completeness: str
    reasons: list[str]
    outgoing_service_tier: object
    returned_service_tier: object
    routing_hint_tier: object
    status: object
    status_origin: object
    upstream_response_id: object
    ingress_request_id: object


def _tier_verdict(out: tuple[object, object], returned: tuple[object, object], reasons: set[str]) -> str | None:
    returned_value, returned_state = returned
    if returned_state not in {"present", "absent"}:
        reasons.add("returned_tier_not_observed" if returned_state == "not_observed" else "returned_tier_unrecognized")
        return None
    out_value, out_state = out
    if out_state == "absent" or (out_state == "present" and out_value not in _PRIORITY_TIERS):
        return "PRIORITY_NOT_SENT"
    if out_state != "present":
        reasons.add("outgoing_tier_not_observed")
        return None
    if returned_state == "absent":
        return "PRIORITY_SENT_TIER_OMITTED"
    if returned_value in _PRIORITY_TIERS:
        return "PRIORITY_SENT_PRIORITY_REPORTED"
    if returned_value == "default":
        return "PRIORITY_SENT_DEFAULT_REPORTED"
    return "PRIORITY_SENT_OTHER_TIER_REPORTED"


def _pair(record: Mapping[str, object], name: str) -> tuple[object, object]:
    return record.get(name), record.get(f"{name}_state")


def classify_capture(records: Iterable[Mapping[str, object]], *, conversation_id: str) -> CaptureClassification:
    """Classify one labelled request; any ambiguity yields ``UNKNOWN``/``PARTIAL``."""
    items = [record for record in records if isinstance(record, Mapping)]
    reasons: set[str] = set()
    path = "unresolved"
    out: tuple[object, object] = (None, "not_observed")
    returned: tuple[object, object] = (None, "not_observed")
    status: object = None
    status_origin: object = None
    hint: object = None
    upstream_response_id: object = None
    ingress: object = None
    if _uuid(conversation_id)[1] != "present":
        reasons.add("invalid_conversation_id")
    if not any(record.get("record") == "capture_open" for record in items):
        reasons.add("capture_open_missing")

    def of(kind: str) -> list[Mapping[str, object]]:
        return [record for record in items if record.get("record") == kind]

    def mine(record: Mapping[str, object]) -> bool:
        return record.get("conversation_id") == conversation_id and record.get("request_kind") == "normal"

    settled = [record for record in of("bridge_request_settled") if mine(record)]
    rows = [record for record in of("request_log_row") if mine(record)]
    if settled:
        path = "http_bridge"
        if len(rows) > 1:
            reasons.add("multiple_eligible_attempts")
        if len(settled) != 1:
            reasons.add("multiple_eligible_attempts")
        else:
            state = settled[0]
            ref = state.get("bridge_request_ref")
            ingress = state.get("ingress_request_id")
            if _ident(ref, _BRIDGE_REF, 45)[1] != "present" or _uuid(ingress)[1] != "present":
                reasons.add("bridge_send_identity_mismatch")
            sends = [
                record
                for record in of("bridge_frame_sent")
                if ref is not None and record.get("bridge_request_ref") == ref
            ]
            if len(sends) != 1 or state.get("attempt_count") != 1 or sends[0].get("attempt_ordinal") != 1:
                reasons.add("bridge_retry_or_send_count")
            else:
                sent = sends[0]
                out = _pair(sent, "outgoing_service_tier")
                if sent.get("ingress_request_id") != ingress or sent.get("conversation_id") != conversation_id:
                    reasons.add("bridge_send_identity_mismatch")
                if sent.get("send_state") != "local_send_completed":
                    reasons.add("bridge_local_send_unproven")
                if sent.get("pending_request_count") != 1:
                    reasons.add("positional_response_match_ambiguous")
                connection_sends = [
                    record
                    for record in of("bridge_frame_sent")
                    if sent.get("connection_ref") is not None
                    and record.get("connection_ref") == sent.get("connection_ref")
                ]
                if len(connection_sends) != 1:
                    # The allowed seams expose a FIFO association, not an
                    # explicit frame-to-response mapping for a shared connection.
                    reasons.add("connection_response_mapping_unresolved")
                opener: Mapping[str, object] = sent
                if sent.get("connection_first_send") is not True:
                    openers = [
                        record
                        for record in of("bridge_frame_sent")
                        if record.get("connection_first_send") is True
                        and sent.get("connection_ref") is not None
                        and record.get("connection_ref") == sent.get("connection_ref")
                    ]
                    opener = openers[0] if len(openers) == 1 else {}
                status, status_origin = opener.get("handshake_status"), opener.get("handshake_status_origin")
                hint = opener.get("opener_hint_tier")
                if status != 101 or status_origin not in _KNOWN_HANDSHAKE_ORIGINS:
                    reasons.add("handshake_status_unknown")
            if state.get("replay_alias_present") is not False:
                reasons.add("replay_alias_present")
            if (
                state.get("upstream_response_id_state") == "present"
                and _ident(state.get("upstream_response_id"), _RESPONSE_ID, 133)[1] == "present"
            ):
                upstream_response_id = state.get("upstream_response_id")
            else:
                reasons.add("upstream_response_id_unresolved")
            returned = _pair(state, "actual_service_tier")
            matched = [row for row in rows if ingress is not None and row.get("ingress_request_id") == ingress]
            if len(matched) != 1:
                reasons.add("request_status_unresolved")
            elif matched[0].get("status") != "success":
                reasons.add("request_not_successful")
    elif len(rows) != 1:
        reasons.add("no_eligible_attempt" if not rows else "multiple_eligible_attempts")
    else:
        row = rows[0]
        ingress, scope = row.get("ingress_request_id"), row.get("scope_id")

        def joined(record: Mapping[str, object]) -> bool:
            if ingress is not None:
                return record.get("ingress_request_id") == ingress
            return scope is not None and record.get("scope_id") == scope

        payloads = [record for record in of("outgoing_payload") if joined(record)]
        posts = [
            hop
            for hop in of("upstream_http_hop")
            if joined(hop) and hop.get("method") == "POST" and hop.get("outcome") == "response"
        ]
        http_payloads = [payload for payload in payloads if payload.get("transport") == "http"]
        if any(payload.get("transport") == "websocket" for payload in payloads):
            path = "direct_websocket"
            reasons.add("raw_websocket_frame_send_unproven")
        else:
            path = "direct_http"
            if len(http_payloads) != 1 or len(posts) != 1:
                reasons.add("http_send_unproven_or_ambiguous")
            else:
                out = _pair(http_payloads[0], "outgoing_service_tier")
                status, status_origin = posts[0].get("status"), posts[0].get("status_origin")
                hint = posts[0].get("routing_hint_tier")
        if row.get("status") != "success":
            reasons.add("request_not_successful")
        returned = _pair(row, "actual_service_tier")
        reasons.add("upstream_response_id_unresolved")
    verdict = "UNKNOWN"
    if path != "unresolved" and not reasons:
        candidate = _tier_verdict(out, returned, reasons)
        if candidate is not None and not reasons:
            verdict = candidate
    return CaptureClassification(
        path=path,
        tier_verdict=verdict,
        completeness="COMPLETE" if not reasons else "PARTIAL",
        reasons=sorted(reasons),
        outgoing_service_tier=out[0],
        returned_service_tier=returned[0],
        routing_hint_tier=hint,
        status=status,
        status_origin=status_origin,
        upstream_response_id=upstream_response_id,
        ingress_request_id=ingress,
    )
