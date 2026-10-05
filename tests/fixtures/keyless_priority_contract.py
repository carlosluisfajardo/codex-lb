"""Provider-free proof for the keyless priority service-tier opt-in.

A self-contained stdlib ``unittest`` module (no pytest, no network). Before the
first ``import app`` it moves HOME and every codex-lb path into a private temp
directory and refuses non-loopback sockets, so a run can never read real state
or reach a provider. Upstream calls are replaced at the proxy facade
(``core_stream_responses``, ``core_compact_responses`` and
``connect_responses_websocket``); every database is a synthetic SQLite file.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import socket
import tempfile
import unittest
import warnings
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

_TMP = Path(tempfile.mkdtemp(prefix="codex-lb-keyless-priority-"))
_DATA = _TMP / "data"
_DB_URL = f"sqlite+aiosqlite:///{_DATA / 'store.db'}"
# The quota registry is a deployment artefact (repository ``config/``); a
# caller running outside a checkout may point at a verified copy.
_KEPT = {"CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE"}
for _name in list(os.environ):
    if _name.upper() in _KEPT:
        continue
    if _name.upper().startswith("CODEX_LB_") or _name.upper() in {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "NETRC",
        "PORT",
        "FORWARDED_ALLOW_IPS",
    }:
        del os.environ[_name]
(_TMP / "home").mkdir()
_DATA.mkdir()
(_TMP / "empty.env").write_text("")
os.environ.update(
    {
        "HOME": str(_TMP / "home"),
        "CODEX_LB_ENV_FILE": str(_TMP / "empty.env"),
        "CODEX_LB_DATA_DIR": str(_DATA),
        "CODEX_LB_DATABASE_URL": _DB_URL,
        "CODEX_LB_ENCRYPTION_KEY_FILE": str(_DATA / "encryption.key"),
        "CODEX_LB_CONVERSATION_ARCHIVE_DIR": str(_DATA / "archive"),
        "CODEX_LB_UPSTREAM_BASE_URL": "http://127.0.0.1:9/backend-api",
        "CODEX_LB_TELEMETRY_ENABLED": "false",
        "CODEX_LB_TELEMETRY_ENDPOINT": "http://127.0.0.1:9",
        "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ENABLED": "false",
        "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID": "keyless-priority-test",
        "CODEX_LB_DATABASE_SQLITE_STARTUP_CHECK_MODE": "off",
        "CODEX_LB_DATABASE_SQLITE_PRE_MIGRATE_BACKUP_ENABLED": "false",
        "CODEX_LB_ENCRYPTION_KEY_FINGERPRINT_MODE": "off",
        "CODEX_LB_EVENT_LOOP_LAG_WARN_THRESHOLD_SECONDS": "0",
        "CODEX_LB_AUTOMATIONS_SCHEDULER_ENABLED": "false",
        "CODEX_LB_AUTH_GUARDIAN_ENABLED": "false",
        "CODEX_LB_RATE_LIMIT_RESET_CREDITS_REFRESH_ENABLED": "false",
    }
)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo


def _require_loopback(address: Any) -> None:
    if isinstance(address, tuple) and address and address[0] not in _LOOPBACK_HOSTS:
        raise ConnectionRefusedError(f"provider-free test refused a connection to {address[0]!r}")


def _guarded_connect(self: socket.socket, address: Any) -> None:
    _require_loopback(address)
    return _real_connect(self, address)


def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
    _require_loopback(address)
    return _real_connect_ex(self, address)


def _guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
    if host not in _LOOPBACK_HOSTS and host is not None:
        raise socket.gaierror(f"provider-free test refused to resolve {host!r}")
    return _real_getaddrinfo(host, *args, **kwargs)


# These guards live only in this dedicated child process.
patch.object(socket.socket, "connect", _guarded_connect).start()
patch.object(socket.socket, "connect_ex", _guarded_connect_ex).start()
patch.object(socket, "getaddrinfo", _guarded_getaddrinfo).start()

import app  # noqa: E402
import app.main as main_module  # noqa: E402
import app.modules.proxy.service as proxy_service_module  # noqa: E402
from app.core.clients.http import close_http_client, init_http_client  # noqa: E402
from app.core.clients.proxy_websocket import UpstreamWebSocketMessage  # noqa: E402
from app.core.config.settings import get_settings  # noqa: E402
from app.core.config.settings_cache import get_settings_cache  # noqa: E402
from app.core.crypto import TokenEncryptor  # noqa: E402
from app.core.openai.model_registry import get_model_registry  # noqa: E402
from app.core.openai.models import CompactResponsePayload  # noqa: E402
from app.core.openai.requests import ResponsesRequest  # noqa: E402
from app.core.utils.time import utcnow  # noqa: E402
from app.db.migrate import check_migration_policy, check_schema_drift, run_upgrade  # noqa: E402
from app.db.models import Account, AccountStatus, RequestLog  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.modules.api_keys.service import ApiKeyData  # noqa: E402
from app.modules.proxy import request_policy  # noqa: E402

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient  # noqa: E402

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

_SOURCE_APP = Path(__file__).resolve().parents[2] / "app"
_MODEL = "gpt-5.4"
_INPUT = [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]
# Absent, null, ``auto`` and ``default`` all leave the tier to upstream.
_RESPONSE_IDS = itertools.count(1)
_DEFAULTED_TIERS: dict[str, dict[str, Any]] = {
    "absent": {},
    "null": {"service_tier": None},
    "auto": {"service_tier": "auto"},
    "default": {"service_tier": "default"},
}


_FRESH_INSTALL_SETTINGS: dict[str, Any] = {}


def setUpModule() -> None:
    assert Path(app.__file__).resolve().parent == _SOURCE_APP.resolve(), app.__file__
    settings = get_settings()
    assert settings.database_url == _DB_URL, settings.database_url
    assert Path(settings.encryption_key_file).resolve().is_relative_to(_TMP.resolve())
    run_upgrade(_DB_URL, "head", bootstrap_legacy=False)
    asyncio.run(_seed_account())
    asyncio.run(_capture_fresh_install_settings())


async def _capture_fresh_install_settings() -> None:
    """Read ``GET /api/settings`` once, before any test writes a setting."""
    instance = _fresh_app()
    async with instance.router.lifespan_context(instance):
        transport = httpx.ASGITransport(app=instance)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/settings")
    assert response.status_code == 200, response.text
    _FRESH_INSTALL_SETTINGS.update(response.json())


async def _seed_account() -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id="acct_keyless_fixture",
                chatgpt_account_id="chatgpt_keyless_fixture",
                email="fixture@example.invalid",
                plan_type="pro",
                access_token_encrypted=encryptor.encrypt("fixture-access"),
                refresh_token_encrypted=encryptor.encrypt("fixture-refresh"),
                id_token_encrypted=encryptor.encrypt("fixture-id"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
            )
        )
        await session.commit()


@asynccontextmanager
async def _minimal_lifespan(app_instance: Any) -> AsyncIterator[None]:
    await get_settings_cache().invalidate(propagate=False)
    await init_http_client()
    try:
        yield
    finally:
        service = getattr(app_instance.state, "proxy_service", None)
        if service is not None:
            await service.drain_persistence_tasks(5.0)
        await close_http_client()


def _fresh_app() -> Any:
    instance = main_module.create_app()
    instance.router.lifespan_context = _minimal_lifespan
    return instance


async def _create_api_key(enforced_service_tier: str | None) -> str:
    from app.modules.api_keys.repository import ApiKeysRepository
    from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService

    async with SessionLocal() as session:
        created = await ApiKeysService(ApiKeysRepository(session)).create_key(
            ApiKeyCreateData(
                name=f"keyed-fixture-{next(_RESPONSE_IDS)}",
                allowed_models=None,
                enforced_service_tier=enforced_service_tier,
            )
        )
    return created.key


async def _set_api_key_auth(enabled: bool) -> None:
    # ``apiKeyAuthEnabled`` is a step-up security field on the API; the synthetic
    # fixture flips the column directly and drops the cached row.
    from app.modules.settings.repository import SettingsRepository

    async with SessionLocal() as session:
        await SettingsRepository(session).update(api_key_auth_enabled=enabled)
    await get_settings_cache().invalidate(propagate=False)


@contextmanager
def _catalog_without_priority() -> Iterator[None]:
    """Make the live model registry report that no model offers ``priority``."""
    registry = get_model_registry()
    with patch.object(registry, "model_advertises_service_tier", lambda _slug, _tier: False):
        yield


@contextmanager
def _patched(name: str, replacement: Any) -> Iterator[None]:
    original = getattr(proxy_service_module, name)
    setattr(proxy_service_module, name, replacement)
    try:
        yield
    finally:
        setattr(proxy_service_module, name, original)


def _completed_event(response_id: str) -> dict[str, Any]:
    return {
        "type": "response.completed",
        "response": {
            "id": response_id,
            "object": "response",
            "status": "completed",
            "model": _MODEL,
            "output": [],
            "service_tier": "default",
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        },
    }


class _StreamUpstream:
    """``core_stream_responses`` stand-in: records the tier, answers ``default``."""

    def __init__(self) -> None:
        self.tiers: list[str | None] = []
        self.payloads: list[dict[str, Any]] = []
        self.response_ids: list[str] = []

    async def __call__(self, payload: ResponsesRequest, *_args: Any, **_kwargs: Any) -> AsyncIterator[str]:
        self.tiers.append(payload.service_tier)
        self.payloads.append(dict(payload.to_payload()))
        response_id = f"resp_keyless_http_{next(_RESPONSE_IDS)}"
        self.response_ids.append(response_id)
        created = {"type": "response.created", "response": {"id": response_id, "status": "in_progress"}}
        yield f"data: {json.dumps(created)}\n\n"
        yield f"data: {json.dumps(_completed_event(response_id))}\n\n"


async def _request_log(request_id: str) -> RequestLog:
    async with SessionLocal() as session:
        row = (await session.execute(select(RequestLog).where(RequestLog.request_id == request_id))).scalar_one()
        session.expunge(row)
        return row


class KeylessPriorityPolicyTests(unittest.TestCase):
    """Unit guards on the request-policy helper and its interplay with existing guards."""

    def _payload(self, service_tier: str | None) -> ResponsesRequest:
        return ResponsesRequest.model_validate(
            {"model": _MODEL, "instructions": "x", "input": _INPUT, "service_tier": service_tier}
        )

    def test_opt_in_requests_priority_when_tier_left_to_upstream(self) -> None:
        for tier in (None, "auto", "default", "AUTO", " Default ", ""):
            with self.subTest(tier=tier):
                payload = self._payload(tier)
                with self.assertLogs(request_policy.logger, "INFO") as logs:
                    applied = request_policy.apply_keyless_priority_service_tier(
                        payload, None, enabled=True, prohibit_fast_mode=False
                    )
                self.assertTrue(applied)
                self.assertEqual(payload.service_tier, "priority")
                self.assertIn(f"client_service_tier={tier} substituted_service_tier=priority", logs.output[0])

    def test_explicit_client_tier_and_disabled_opt_in_are_untouched(self) -> None:
        for enabled, tier, expected in (
            (True, "flex", "flex"),
            (True, "priority", "priority"),
            (True, "fast", "priority"),
            (False, None, None),
            (False, "default", "default"),
        ):
            with self.subTest(enabled=enabled, tier=tier):
                payload = self._payload(tier)
                applied = request_policy.apply_keyless_priority_service_tier(
                    payload, None, enabled=enabled, prohibit_fast_mode=False
                )
                self.assertFalse(applied)
                self.assertEqual(payload.service_tier, expected)

    def test_prohibit_fast_mode_veto_wins(self) -> None:
        for tier in (None, "auto", "default"):
            with self.subTest(tier=tier):
                payload = self._payload(tier)
                applied = request_policy.apply_keyless_priority_service_tier(
                    payload, None, enabled=True, prohibit_fast_mode=True
                )
                self.assertFalse(applied)
                self.assertEqual(payload.service_tier, tier)

    def test_keyed_request_keeps_its_api_key_policy(self) -> None:
        api_key = ApiKeyData.__new__(ApiKeyData)
        payload = self._payload("default")
        applied = request_policy.apply_keyless_priority_service_tier(
            payload, api_key, enabled=True, prohibit_fast_mode=False
        )
        self.assertFalse(applied)
        self.assertEqual(payload.service_tier, "default")

    def test_overflow_projection_still_strips_injected_priority(self) -> None:
        from app.db.models import ModelSource
        from app.modules.proxy.api import _shape_source_responses_payload

        payload = self._payload(None)
        request_policy.apply_keyless_priority_service_tier(payload, None, enabled=True, prohibit_fast_mode=False)
        source = ModelSource(id="src_overflow", name="overflow", base_url="http://127.0.0.1:9/v1")
        overflow_body = _shape_source_responses_payload(payload, source, api_key=None, strip_service_tier=True)
        self.assertEqual(payload.service_tier, "priority")
        self.assertNotIn("service_tier", overflow_body)

    def test_catalog_fallback_still_drops_injected_priority(self) -> None:
        payload = self._payload(None)
        applied = request_policy.apply_keyless_priority_service_tier(
            payload, None, enabled=True, prohibit_fast_mode=False
        )
        with _catalog_without_priority():
            dropped = request_policy.apply_enforced_service_tier_model_fallback(
                payload, service_tier_was_enforced=applied, registry=get_model_registry()
            )
        self.assertTrue(dropped)
        self.assertIsNone(payload.service_tier)


class KeylessPriorityHttpTests(unittest.IsolatedAsyncioTestCase):
    """Settings surface and HTTP forwarding through the real keyless routes."""

    async def asyncSetUp(self) -> None:
        self.app = _fresh_app()
        self._lifespan = self.app.router.lifespan_context(self.app)
        await self._lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver")
        await self._put_settings(keylessPriorityServiceTier=False, prohibitFastMode=False)

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self._lifespan.__aexit__(None, None, None)

    async def _put_settings(self, **fields: Any) -> dict[str, Any]:
        response = await self.client.put("/api/settings", json=fields)
        self.assertEqual(response.status_code, 200, response.text)
        await get_settings_cache().invalidate(propagate=False)
        return response.json()

    async def _post_stream(self, path: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        request_headers = {"accept": "text/event-stream", **(headers or {})}
        async with self.client.stream("POST", path, json=body, headers=request_headers) as response:
            self.assertEqual(response.status_code, 200)
            text = "".join([chunk async for chunk in response.aiter_text()])
        self.assertIn("response.completed", text)

    async def _forwarded_tiers(
        self, path: str, variants: dict[str, dict[str, Any]], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        upstream = _StreamUpstream()
        with _patched("core_stream_responses", upstream):
            for extra in variants.values():
                body = {"model": _MODEL, "instructions": "x", "input": _INPUT, "stream": True, **extra}
                await self._post_stream(path, body, headers)
        await self.app.state.proxy_service.drain_persistence_tasks(5.0)
        self.response_ids = upstream.response_ids
        self.payloads = upstream.payloads
        return dict(zip(variants, upstream.tiers, strict=True))

    async def test_setting_defaults_off_and_persists_through_partial_updates(self) -> None:
        self.assertIs(_FRESH_INSTALL_SETTINGS.get("keylessPriorityServiceTier"), False)
        self.assertIs((await self._put_settings(keylessPriorityServiceTier=True))["keylessPriorityServiceTier"], True)
        self.assertIs((await self.client.get("/api/settings")).json()["keylessPriorityServiceTier"], True)
        partial = await self._put_settings(prohibitFastMode=False)
        self.assertIs(partial["keylessPriorityServiceTier"], True)
        self.assertIs((await self._put_settings(keylessPriorityServiceTier=False))["keylessPriorityServiceTier"], False)

    async def test_opt_in_on_requests_priority_on_codex_and_v1_routes(self) -> None:
        await self._put_settings(keylessPriorityServiceTier=True)
        for path in ("/backend-api/codex/responses", "/v1/responses"):
            with self.subTest(path=path):
                tiers = await self._forwarded_tiers(path, _DEFAULTED_TIERS)
                self.assertEqual(tiers, dict.fromkeys(_DEFAULTED_TIERS, "priority"))

    async def test_opt_in_off_preserves_baseline_forwarding(self) -> None:
        tiers = await self._forwarded_tiers("/backend-api/codex/responses", _DEFAULTED_TIERS)
        self.assertEqual(tiers, {"absent": None, "null": None, "auto": "auto", "default": "default"})

    async def test_responses_atomic_toggle_does_not_mix_policy_rows(self) -> None:
        import app.modules.proxy.api as api_module

        original = api_module._select_responses_model_source_with_continuity

        async def toggle_during_selection(*args: Any, **kwargs: Any) -> Any:
            selection = await original(*args, **kwargs)
            await self._put_settings(keylessPriorityServiceTier=True, prohibitFastMode=True)
            return selection

        with patch.object(api_module, "_select_responses_model_source_with_continuity", toggle_during_selection):
            for path in ("/backend-api/codex/responses", "/v1/responses"):
                with self.subTest(path=path):
                    await self._put_settings(keylessPriorityServiceTier=False, prohibitFastMode=False)
                    tiers = await self._forwarded_tiers(path, {"absent": {}})
                    # Neither atomic row permits priority: old opt-in is off, new veto is on.
                    self.assertEqual(tiers, {"absent": None})

    async def test_chat_atomic_toggle_does_not_mix_policy_rows(self) -> None:
        import app.modules.proxy.api as api_module

        original = api_module._select_chat_model_source

        async def toggle_during_selection(*args: Any, **kwargs: Any) -> Any:
            selection = await original(*args, **kwargs)
            await self._put_settings(keylessPriorityServiceTier=True, prohibitFastMode=True)
            return selection

        upstream = _StreamUpstream()
        with patch.object(api_module, "_select_chat_model_source", toggle_during_selection):
            with _patched("core_stream_responses", upstream):
                response = await self.client.post(
                    "/v1/chat/completions",
                    json={"model": _MODEL, "messages": [{"role": "user", "content": "hi"}], "stream": True},
                )
                self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(upstream.tiers, [None])

    async def test_compact_atomic_toggle_does_not_mix_policy_rows(self) -> None:
        import app.modules.proxy.api as api_module

        original = api_module._compact_responses
        tiers: list[str | None] = []

        async def toggle_before_compact(*args: Any, **kwargs: Any) -> Any:
            await self._put_settings(keylessPriorityServiceTier=True, prohibitFastMode=True)
            return await original(*args, **kwargs)

        async def fake_compact(payload: Any, *_args: Any, **_kwargs: Any) -> CompactResponsePayload:
            tiers.append(payload.service_tier)
            return CompactResponsePayload.model_validate(
                {"object": "response.compaction", "id": "cmp_snapshot", "output": [], "service_tier": "default"}
            )

        with patch.object(api_module, "_compact_responses", toggle_before_compact):
            with _patched("core_compact_responses", fake_compact):
                for path in ("/backend-api/codex/responses/compact", "/v1/responses/compact"):
                    await self._put_settings(keylessPriorityServiceTier=False, prohibitFastMode=False)
                    response = await self.client.post(
                        path, json={"model": _MODEL, "instructions": "x", "input": _INPUT}
                    )
                    self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(tiers, [None, None])

    async def test_request_log_keeps_requested_and_upstream_actual_tiers_apart(self) -> None:
        await self._put_settings(keylessPriorityServiceTier=True)
        await self._forwarded_tiers("/backend-api/codex/responses", {"absent": {}})
        log = await _request_log(self.response_ids[0])
        self.assertIsNone(log.api_key_id)
        self.assertEqual(log.requested_service_tier, "priority")
        self.assertEqual(log.actual_service_tier, "default")
        self.assertEqual(log.service_tier, "default")

    async def test_catalog_fallback_drops_injected_priority_on_the_route(self) -> None:
        await self._put_settings(keylessPriorityServiceTier=True)
        with _catalog_without_priority():
            tiers = await self._forwarded_tiers(
                "/backend-api/codex/responses", {"absent": {}, "default": {"service_tier": "default"}}
            )
        self.assertEqual(tiers, {"absent": None, "default": None})

    async def test_prohibit_fast_mode_veto_and_explicit_tier_win_over_opt_in(self) -> None:
        await self._put_settings(keylessPriorityServiceTier=True)
        explicit = await self._forwarded_tiers("/backend-api/codex/responses", {"flex": {"service_tier": "flex"}})
        self.assertEqual(explicit, {"flex": "flex"})
        await self._put_settings(prohibitFastMode=True)
        vetoed = await self._forwarded_tiers(
            "/backend-api/codex/responses", {"absent": {}, "default": {"service_tier": "default"}}
        )
        self.assertEqual(vetoed, {"absent": None, "default": "default"})

    async def test_opt_in_on_requests_priority_for_chat_completions(self) -> None:
        await self._put_settings(keylessPriorityServiceTier=True)
        upstream = _StreamUpstream()
        body = {"model": _MODEL, "messages": [{"role": "user", "content": "hi"}], "stream": True}
        with _patched("core_stream_responses", upstream):
            async with self.client.stream("POST", "/v1/chat/completions", json=body) as response:
                self.assertEqual(response.status_code, 200)
                await response.aread()
        self.assertEqual(upstream.tiers, ["priority"])

    async def test_owner_keeps_the_signed_forwarded_tier(self) -> None:
        from app.modules.proxy.http_bridge_forwarding import HTTPBridgeForwardContext, build_owner_forward_headers

        context = HTTPBridgeForwardContext(
            origin_instance="keyless-priority-origin",
            target_instance=get_settings().http_responses_session_bridge_instance_id,
            codex_session_affinity=False,
            downstream_turn_state=None,
        )
        upstream = _StreamUpstream()
        # The origin's signed decision wins in both directions: an owner with the
        # opt-in ON adds nothing, and an owner with it OFF removes nothing.
        for owner_opt_in, forwarded_tier in ((True, None), (False, "priority")):
            await self._put_settings(keylessPriorityServiceTier=owner_opt_in)
            payload = ResponsesRequest.model_validate(
                {"model": _MODEL, "instructions": "x", "input": _INPUT, "stream": True, "service_tier": forwarded_tier}
            )
            headers = build_owner_forward_headers(headers={}, payload=payload, context=context)
            with _patched("core_stream_responses", upstream):
                async with self.client.stream(
                    "POST",
                    "/internal/bridge/responses",
                    json=payload.model_dump_for_forwarding(),
                    headers={**headers, "accept": "text/event-stream"},
                ) as response:
                    self.assertEqual(response.status_code, 200, await response.aread())
                    await response.aread()
        self.assertEqual(upstream.tiers, [None, "priority"])

    async def test_opt_in_on_reaches_the_http_bridge_upstream(self) -> None:
        # Production default: HTTP requests may ride the upstream WebSocket bridge.
        settings = get_settings()
        settings.http_responses_session_bridge_enabled = True
        upstream = _WebSocketUpstream()
        try:
            await self._put_settings(keylessPriorityServiceTier=True, httpDownstreamTransportPolicy="always_websocket")
            with _patched("connect_responses_websocket", upstream.connect):
                await self._post_stream(
                    "/backend-api/codex/responses",
                    {"model": _MODEL, "instructions": "x", "input": _INPUT, "stream": True, "service_tier": "default"},
                )
            await self.app.state.proxy_service.drain_persistence_tasks(5.0)
        finally:
            settings.http_responses_session_bridge_enabled = False
            await self._put_settings(httpDownstreamTransportPolicy="smart")
        self.assertEqual(upstream.frames[0].get("service_tier"), "priority")
        self.assertEqual(upstream.routing_hints, [(_MODEL, "priority")])
        log = await _request_log(upstream.response_ids[0])
        self.assertEqual((log.requested_service_tier, log.actual_service_tier), ("priority", "default"))

    async def test_opt_in_changes_only_the_service_tier(self) -> None:
        body = {"reasoning": {"effort": "high"}}
        await self._forwarded_tiers("/backend-api/codex/responses", {"off": body})
        (off_payload,) = self.payloads
        await self._forwarded_tiers("/backend-api/codex/responses", {"fast": {**body, "service_tier": "priority"}})
        (client_fast_payload,) = self.payloads
        await self._put_settings(keylessPriorityServiceTier=True)
        await self._forwarded_tiers("/backend-api/codex/responses", {"on": body})
        (on_payload,) = self.payloads
        # The opt-in turns a keyless request into exactly what a client asking for Fast sends.
        self.assertEqual(on_payload, client_fast_payload)
        self.assertEqual({k: v for k, v in on_payload.items() if k != "service_tier"}, off_payload)
        self.assertEqual((on_payload["model"], on_payload["reasoning"]), (_MODEL, {"effort": "high"}))

    async def test_direct_model_source_keeps_the_client_tier(self) -> None:
        from fastapi.responses import JSONResponse

        import app.modules.proxy.api as api_module
        from app.db.models import ModelSource

        source = ModelSource(id="src_direct", name="direct", base_url="http://127.0.0.1:9/v1")
        seen: list[str | None] = []

        async def select_source(*_args: Any, **_kwargs: Any) -> Any:
            return (source, _MODEL), False

        async def no_overflow(*_args: Any, **_kwargs: Any) -> None:
            return None

        async def source_response(_request: Any, payload: ResponsesRequest, **_kwargs: Any) -> JSONResponse:
            seen.append(payload.service_tier)
            return JSONResponse({"ok": True})

        await self._put_settings(keylessPriorityServiceTier=True)
        replacements = {
            "_select_responses_model_source_with_continuity": select_source,
            "resolve_subscription_overflow": no_overflow,
            "_source_responses_response": source_response,
        }
        originals = {name: getattr(api_module, name) for name in replacements}
        try:
            for name, replacement in replacements.items():
                setattr(api_module, name, replacement)
            for path in ("/backend-api/codex/responses", "/v1/responses"):
                for extra in ({}, {"service_tier": "default"}):
                    body = {"model": _MODEL, "instructions": "x", "input": _INPUT, "stream": True, **extra}
                    response = await self.client.post(path, json=body)
                    self.assertEqual(response.status_code, 200, response.text)
        finally:
            for name, original in originals.items():
                setattr(api_module, name, original)
        self.assertEqual(seen, [None, "default", None, "default"])

    async def test_keyed_requests_keep_their_api_key_policy_on_the_route(self) -> None:
        await self._put_settings(keylessPriorityServiceTier=True)
        plain_key = await _create_api_key(None)
        enforced_default_key = await _create_api_key("default")
        await _set_api_key_auth(True)
        try:
            plain = await self._forwarded_tiers(
                "/backend-api/codex/responses", {"absent": {}}, {"authorization": f"Bearer {plain_key}"}
            )
            enforced = await self._forwarded_tiers(
                "/backend-api/codex/responses", {"absent": {}}, {"authorization": f"Bearer {enforced_default_key}"}
            )
        finally:
            await _set_api_key_auth(False)
        self.assertEqual((plain, enforced), ({"absent": None}, {"absent": None}))

    async def test_opt_in_on_requests_priority_for_compact(self) -> None:
        tiers: list[str | None] = []

        async def fake_compact(payload: Any, *_args: Any, **_kwargs: Any) -> CompactResponsePayload:
            tiers.append(payload.service_tier)
            return CompactResponsePayload.model_validate(
                {"object": "response.compaction", "id": "cmp_keyless", "output": [], "service_tier": "default"}
            )

        await self._put_settings(keylessPriorityServiceTier=True)
        with _patched("core_compact_responses", fake_compact):
            response = await self.client.post(
                "/backend-api/codex/responses/compact",
                json={"model": _MODEL, "instructions": "x", "input": _INPUT},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(tiers, ["priority"])


class _WebSocketUpstream:
    """``connect_responses_websocket`` stand-in answering every create with tier ``default``."""

    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []
        self.response_ids: list[str] = []
        self.routing_hints: list[Any] = []
        self._queue: asyncio.Queue[UpstreamWebSocketMessage] | None = None

    async def connect(self, *_args: Any, **kwargs: Any) -> "_WebSocketUpstream":
        self.routing_hints.append(kwargs.get("routing_hint"))
        self._queue = asyncio.Queue()
        return self

    async def send_text(self, text: str) -> None:
        frame = json.loads(text)
        self.frames.append(frame)
        if frame.get("type") != "response.create":
            return
        assert self._queue is not None
        response_id = f"resp_keyless_ws_{next(_RESPONSE_IDS)}"
        self.response_ids.append(response_id)
        created = {"type": "response.created", "response": {"id": response_id, "status": "in_progress"}}
        await self._queue.put(UpstreamWebSocketMessage(kind="text", text=json.dumps(created)))
        await self._queue.put(UpstreamWebSocketMessage(kind="text", text=json.dumps(_completed_event(response_id))))

    async def send_bytes(self, data: bytes) -> None:
        return None

    async def receive(self) -> UpstreamWebSocketMessage:
        assert self._queue is not None
        return await self._queue.get()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        return None

    def response_header(self, name: str) -> str | None:
        return None


class KeylessPriorityWebSocketTests(unittest.TestCase):
    """Native WebSocket ``response.create`` forwarding through the keyless route."""

    def _run(
        self, settings: dict[str, Any], extra: dict[str, Any], api_key_tier: str | None | bool = False
    ) -> tuple[_WebSocketUpstream, RequestLog]:
        """Send one ``response.create``; an ``api_key_tier`` other than False authenticates with a fresh key."""
        upstream = _WebSocketUpstream()
        instance = _fresh_app()
        frame = {"type": "response.create", "model": _MODEL, "instructions": "x", "input": _INPUT, **extra}
        with _patched("connect_responses_websocket", upstream.connect):
            with TestClient(instance, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
                portal = client.portal
                assert portal is not None
                response = client.put("/api/settings", json=settings)
                self.assertEqual(response.status_code, 200, response.text)
                headers: dict[str, str] = {}
                if api_key_tier is not False:
                    headers["authorization"] = f"Bearer {portal.call(_create_api_key, api_key_tier)}"
                    portal.call(_set_api_key_auth, True)
                try:
                    url = "ws://localhost/backend-api/codex/responses"
                    with client.websocket_connect(url, headers=headers) as websocket:
                        websocket.send_text(json.dumps(frame))
                        while json.loads(websocket.receive_text()).get("type") != "response.completed":
                            pass
                finally:
                    if headers:
                        portal.call(_set_api_key_auth, False)
                portal.call(instance.state.proxy_service.drain_persistence_tasks, 5.0)
                log = portal.call(_request_log, upstream.response_ids[0])
        return upstream, log

    def test_opt_in_on_requests_priority_and_logs_upstream_actual(self) -> None:
        for name, extra in _DEFAULTED_TIERS.items():
            with self.subTest(tier=name):
                upstream, log = self._run({"keylessPriorityServiceTier": True, "prohibitFastMode": False}, extra)
                self.assertEqual(upstream.frames[0].get("service_tier"), "priority")
                self.assertEqual(upstream.routing_hints, [(_MODEL, "priority")])
                self.assertEqual(log.requested_service_tier, "priority")
                self.assertEqual(log.actual_service_tier, "default")

    def test_catalog_fallback_drops_injected_priority_on_the_websocket(self) -> None:
        with _catalog_without_priority():
            upstream, log = self._run({"keylessPriorityServiceTier": True, "prohibitFastMode": False}, {})
        self.assertNotIn("service_tier", upstream.frames[0])
        self.assertIsNone(log.requested_service_tier)

    def test_opt_in_off_and_prohibit_veto_keep_baseline_frame(self) -> None:
        for settings in (
            {"keylessPriorityServiceTier": False, "prohibitFastMode": False},
            {"keylessPriorityServiceTier": True, "prohibitFastMode": True},
        ):
            with self.subTest(settings=settings):
                upstream, log = self._run(settings, {})
                self.assertNotIn("service_tier", upstream.frames[0])
                self.assertIsNone(log.requested_service_tier)
                self.assertEqual(log.actual_service_tier, "default")

    def test_keyed_websocket_keeps_its_api_key_policy(self) -> None:
        for enforced_tier in (None, "default"):
            with self.subTest(enforced_service_tier=enforced_tier):
                upstream, log = self._run({"keylessPriorityServiceTier": True}, {}, api_key_tier=enforced_tier)
                self.assertNotIn("service_tier", upstream.frames[0])
                self.assertIsNotNone(log.api_key_id)
                self.assertIsNone(log.requested_service_tier)

    def test_opt_in_frame_equals_a_client_fast_frame(self) -> None:
        extra = {"reasoning": {"effort": "high"}}
        off, _ = self._run({"keylessPriorityServiceTier": False}, extra)
        client_fast, _ = self._run({"keylessPriorityServiceTier": False}, {**extra, "service_tier": "priority"})
        on, _ = self._run({"keylessPriorityServiceTier": True}, extra)
        self.assertEqual(on.frames[0], client_fast.frames[0])
        self.assertEqual({k: v for k, v in on.frames[0].items() if k != "service_tier"}, off.frames[0])
        self.assertEqual((on.frames[0]["model"], on.frames[0]["reasoning"]), (_MODEL, {"effort": "high"}))


class KeylessPriorityMigrationTests(unittest.TestCase):
    """Forward migration, default for an existing row, and rollback on a synthetic DB."""

    _REVISION = "20261005_000000_add_dashboard_keyless_priority_service_tier"
    _PARENT = "20260912_000000_merge_thread_cache_and_bridge_retirement_heads"

    def test_old_row_upgrades_to_false_and_downgrade_round_trips(self) -> None:
        import sqlite3

        from alembic import command

        from app.db.migrate import _build_alembic_config

        db_path = _TMP / "migration.db"
        url = f"sqlite+aiosqlite:///{db_path}"
        run_upgrade(url, self._PARENT, bootstrap_legacy=False)
        with sqlite3.connect(db_path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(dashboard_settings)")}
            self.assertNotIn("keyless_priority_service_tier", columns)
            # The migration chain seeds the singleton row; mark it so the upgrade must keep it.
            marked = connection.execute("UPDATE dashboard_settings SET prohibit_fast_mode = 1 WHERE id = 1")
            self.assertEqual(marked.rowcount, 1)
        run_upgrade(url, "head", bootstrap_legacy=False)
        with sqlite3.connect(db_path) as connection:
            row = connection.execute(
                "SELECT prohibit_fast_mode, keyless_priority_service_tier FROM dashboard_settings WHERE id = 1"
            ).fetchone()
            version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        self.assertEqual(row, (1, 0))
        from alembic.script import ScriptDirectory

        self.assertEqual(version, ScriptDirectory.from_config(_build_alembic_config(url)).get_current_head())
        self.assertEqual(check_schema_drift(url), ())
        self.assertEqual(check_migration_policy(url), ())

        command.downgrade(_build_alembic_config(url), self._PARENT)
        with sqlite3.connect(db_path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(dashboard_settings)")}
            kept = connection.execute("SELECT prohibit_fast_mode FROM dashboard_settings WHERE id = 1").fetchone()
        self.assertNotIn("keyless_priority_service_tier", columns)
        self.assertEqual(kept, (1,))

        run_upgrade(url, "head", bootstrap_legacy=False)
        self.assertEqual(check_schema_drift(url), ())


if __name__ == "__main__":
    unittest.main()
