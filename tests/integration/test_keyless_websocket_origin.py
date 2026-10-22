from __future__ import annotations

import json
from typing import cast

import pytest
from fastapi import FastAPI, WebSocket
from starlette.types import Message, Scope

import app.modules.proxy.api as proxy_api
from app.core.config.settings_cache import get_settings_cache
from app.db.session import SessionLocal
from app.modules.proxy.service import ProxyService
from app.modules.settings.repository import SettingsRepository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses", "/backend-api/codex/v1/responses"])
@pytest.mark.parametrize(
    ("browser_headers", "allowed", "keyed"),
    [
        ([(b"origin", b"https://foreign.example")], False, False),
        ([(b"origin", b"null")], False, False),
        ([(b"origin", b"http://localhost")], True, False),
        ([], True, False),
        ([(b"origin", b"https://foreign.example"), (b"sec-fetch-site", b"same-origin")], False, False),
        ([(b"origin", b"http://localhost"), (b"origin", b"https://foreign.example")], False, False),
        ([(b"origin", b"http://localhost:0")], False, False),
        ([(b"origin", b"http://localhost:80")], True, False),
        ([(b"origin", b"http://localhost:8080")], False, False),
        ([(b"origin", b"http://user@localhost")], False, False),
        ([(b"sec-fetch-site", b"cross-site")], False, False),
        ([(b"sec-fetch-site", b"none")], True, False),
        ([(b"origin", b"https://foreign.example")], True, True),
    ],
)
async def test_keyless_browser_origin_is_checked_before_websocket_accept(
    app_instance: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    browser_headers: list[tuple[bytes, bytes]],
    allowed: bool,
    keyed: bool,
    async_client,
) -> None:
    async def allow_transport() -> None:
        return None

    async def allow_overflow(*args: object, **kwargs: object) -> None:
        return None

    dispatched: list[bool] = []

    async def inert_service(self: ProxyService, websocket: WebSocket, *args: object, **kwargs: object) -> None:
        dispatched.append(True)
        await websocket.close()

    monkeypatch.setattr(proxy_api, "_websocket_upstream_transport_denial", allow_transport)
    monkeypatch.setattr(proxy_api, "handshake_denial", allow_overflow)
    monkeypatch.setattr(ProxyService, "proxy_responses_websocket", inert_service)
    messages: list[Message] = []
    authorization_headers = []
    if keyed:
        response = await async_client.post("/api/api-keys/", json={"name": "origin fixture"})
        assert response.status_code == 200
        authorization_headers = [(b"authorization", f"Bearer {response.json()['key']}".encode())]
        async with SessionLocal() as session:
            settings = await SettingsRepository(session).get_or_create()
            settings.api_key_auth_enabled = True
            await session.commit()
        await get_settings_cache().invalidate()

    async def receive() -> Message:
        return {"type": "websocket.connect"}

    async def send(message: Message) -> None:
        messages.append(message)

    scope = cast(
        Scope,
        {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "scheme": "ws",
            "server": ("localhost", 80),
            "client": ("127.0.0.1", 50001),
            "root_path": "",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "subprotocols": [],
            "state": {},
            "headers": [(b"host", b"localhost"), *browser_headers, *authorization_headers],
            "extensions": {"websocket.http.response": {}},
        },
    )
    async with app_instance.router.lifespan_context(app_instance):
        await app_instance(scope, receive, send)

    assert any(message["type"] == "websocket.accept" for message in messages) is allowed
    assert bool(dispatched) is allowed
    if not allowed:
        start = next(message for message in messages if message["type"] == "websocket.http.response.start")
        assert start["status"] == 403
        body = b"".join(
            message.get("body", b"") for message in messages if message["type"] == "websocket.http.response.body"
        )
        assert json.loads(body)["error"]["code"] == "cross_site_request_rejected"
