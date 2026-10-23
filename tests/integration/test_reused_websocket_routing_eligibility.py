from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.modules.accounts.service as accounts_service
import app.modules.proxy.api as proxy_api
import app.modules.proxy.service as proxy_service
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.proxy.account_cache import get_routing_availability_cache
from tests.integration.test_proxy_websocket_responses import (
    _SequencedUpstreamWebSocket,
    _websocket_response_batch,
    _websocket_settings,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("intervention", ["eligible", "pause", "hold"])
@pytest.mark.parametrize("block_at", ["before_second_frame", "during_account_admission"])
def test_retained_direct_socket_rechecks_serving_eligibility(
    app_instance, monkeypatch, intervention, block_at, request
):
    account_id = "owned-websocket-fixture"
    upstream = _SequencedUpstreamWebSocket(
        [], deferred_message_batches=[_websocket_response_batch("first"), _websocket_response_batch("second")]
    )
    selected = []
    propagation = AsyncMock(return_value=True)
    admission_entered, release_admission = asyncio.Event(), asyncio.Event()
    admission_calls = 0
    original_admission = proxy_service.ProxyService._acquire_account_response_create_lease_or_overload

    async def gated_admission(self, **kwargs):
        nonlocal admission_calls
        admission_calls += 1
        if block_at == "during_account_admission" and admission_calls == 2:
            admission_entered.set()
            await release_admission.wait()
        return await original_admission(self, **kwargs)

    async def wait_for_admission():
        await asyncio.wait_for(admission_entered.wait(), 2)

    def require_fixture_cleanup():
        assert upstream.closed, "owned fake upstream was not closed by the test client context"

    request.addfinalizer(require_fixture_cleanup)

    class Settings:
        async def get(self):
            return _websocket_settings()

    async def seed():
        encryptor = TokenEncryptor()
        async with SessionLocal() as session:
            session.add(
                Account(
                    id=account_id,
                    email="websocket@example.invalid",
                    chatgpt_account_id="fixture-workspace",
                    plan_type="plus",
                    status=AccountStatus.ACTIVE,
                    access_token_encrypted=encryptor.encrypt("fixture-access"),
                    refresh_token_encrypted=encryptor.encrypt("fixture-refresh"),
                    id_token_encrypted=encryptor.encrypt("fixture-id"),
                    last_refresh=utcnow(),
                )
            )
            await session.commit()

    async def connect(self, headers, **kwargs):
        async with SessionLocal() as session:
            account = await session.get(Account, account_id)
            assert account is not None
            selected.append(account)
            return account, upstream

    async def allow_firewall(websocket):
        return None

    async def allow_key(authorization, *, request=None):
        return None

    monkeypatch.setattr(proxy_api, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api, "validate_proxy_api_key_authorization", allow_key)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: Settings())
    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", connect)
    monkeypatch.setattr(proxy_service.ProxyService, "_write_request_log", AsyncMock())
    monkeypatch.setattr(accounts_service, "propagate_account_routing_change", propagation)
    monkeypatch.setattr(
        proxy_service.ProxyService, "_acquire_account_response_create_lease_or_overload", gated_admission
    )

    with TestClient(app_instance, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None
        client.portal.call(seed)
        client.portal.call(get_routing_availability_cache().refresh_from_db)
        with client.websocket_connect("/v1/responses") as websocket:
            websocket.send_json({"type": "response.create", "model": "gpt-5.4", "input": "first"})
            first = [json.loads(websocket.receive_text()) for _ in range(2)]
            assert [event["type"] for event in first] == ["response.created", "response.completed"]
            assert len(selected) == 1 and len(upstream.sent_text) == 1
            if block_at == "during_account_admission":
                websocket.send_json({"type": "response.create", "model": "gpt-5.4", "input": "second"})
                client.portal.call(wait_for_admission)
            if intervention == "pause":
                response = client.post(f"/api/accounts/{account_id}/pause")
                assert response.status_code == 200, response.text
            elif intervention == "hold":
                response = client.put(
                    f"/api/accounts/{account_id}/routing-hold",
                    json={
                        "held": True,
                        "expected": {"status": "active", "routingPolicy": "normal", "routingHeld": False},
                    },
                )
                assert response.status_code == 200, response.text
            if intervention != "eligible":
                propagation.assert_awaited()
                assert get_routing_availability_cache().is_unavailable(account_id)
            if block_at == "during_account_admission":
                client.portal.call(release_admission.set)
            else:
                websocket.send_json({"type": "response.create", "model": "gpt-5.4", "input": "second"})
            second = json.loads(websocket.receive_text())
            if intervention == "eligible":
                terminal = json.loads(websocket.receive_text())
                assert second["type"] == "response.created" and terminal["type"] == "response.completed"
                assert len(selected) == 1 and len(upstream.sent_text) == 2
            else:
                assert len(upstream.sent_text) == 1, (
                    "retained direct socket dispatched fresh work after successful routing block"
                )
                assert second["type"] in {"error", "response.failed"}
                assert len(selected) == 1 and not upstream.closed
                if intervention == "pause":
                    resumed = client.post(f"/api/accounts/{account_id}/reactivate")
                else:
                    resumed = client.put(
                        f"/api/accounts/{account_id}/routing-hold",
                        json={
                            "held": False,
                            "expected": {"status": "active", "routingPolicy": "normal", "routingHeld": True},
                        },
                    )
                assert resumed.status_code == 200, resumed.text
                assert not get_routing_availability_cache().is_unavailable(account_id)
                websocket.send_json({"type": "response.create", "model": "gpt-5.4", "input": "resumed"})
                resumed_events = [json.loads(websocket.receive_text()) for _ in range(2)]
                assert [event["type"] for event in resumed_events] == ["response.created", "response.completed"]
                assert len(selected) == 1 and len(upstream.sent_text) == 2
    assert upstream.closed
