from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import update

from app.core.clients.usage import UsageFetchError
from app.core.crypto import TokenEncryptor
from app.core.usage.models import UsagePayload
from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountStatus,
    HttpBridgeSessionRecord,
    HttpBridgeSessionState,
    StickySession,
    StickySessionKind,
)
from app.db.session import SessionLocal
from app.modules.accounts.mappers import _extract_credit_status as dashboard_credits
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.load_balancer import _extract_credit_status as routing_credits
from app.modules.usage import updater as updater_module
from app.modules.usage.repository import UsageRepository
from app.modules.usage.updater import UsageUpdater

pytestmark = pytest.mark.integration


def _account() -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id="usage-integrity",
        chatgpt_account_id="fixture-workspace",
        email="usage@example.invalid",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("old-access"),
        refresh_token_encrypted=encryptor.encrypt("old-refresh"),
        id_token_encrypted=encryptor.encrypt("fixture-id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("error_code", ["app_session_terminated", "account_deactivated"])
@pytest.mark.parametrize("mutate_loaded_account", [False, True])
async def test_delayed_usage_failure_cannot_overwrite_reauthentication(
    _reset_db_state: bool, monkeypatch: pytest.MonkeyPatch, error_code: str, mutate_loaded_account: bool
) -> None:
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed_failure(**kwargs: object) -> UsagePayload:
        assert kwargs["access_token"] == "old-access"
        started.set()
        await release.wait()
        raise UsageFetchError(401, "fixture permanent failure", code=error_code)

    monkeypatch.setattr(updater_module, "fetch_usage", delayed_failure)
    async with SessionLocal() as session:
        account = _account()
        session.add(account)
        await session.flush()
        session.add(StickySession(key="retained-pin", kind=StickySessionKind.CODEX_SESSION, account_id=account.id))
        session.add(
            HttpBridgeSessionRecord(
                id="retained-bridge",
                session_key_kind="codex_session",
                session_key_value="retained-pin",
                session_key_hash="fixture-hash",
                api_key_scope="",
                account_id=account.id,
                state=HttpBridgeSessionState.ACTIVE,
            )
        )
        await session.commit()
        updater = UsageUpdater(UsageRepository(session), AccountsRepository(session))
        pending = asyncio.create_task(updater._refresh_account(account, usage_account_id=account.chatgpt_account_id))
        try:
            await asyncio.wait_for(started.wait(), 2)
            encrypted_refresh = TokenEncryptor().encrypt("repaired-refresh")
            async with SessionLocal() as repair:
                await repair.execute(
                    update(Account)
                    .where(Account.id == account.id)
                    .values(
                        access_token_encrypted=TokenEncryptor().encrypt("repaired-access"),
                        refresh_token_encrypted=encrypted_refresh,
                        status=AccountStatus.ACTIVE,
                    )
                )
                await repair.commit()
            if mutate_loaded_account:
                # A caller may refresh this same ORM object while the old request waits.
                account.refresh_token_encrypted = encrypted_refresh
            release.set()
            result = await asyncio.wait_for(pending, 2)
            assert result.fetch_succeeded is False
        finally:
            release.set()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    async with SessionLocal() as session:
        stored = await session.get(Account, "usage-integrity")
        assert stored is not None and stored.status == AccountStatus.ACTIVE
        assert stored.refresh_token_encrypted == encrypted_refresh
        assert await session.get(StickySession, ("retained-pin", StickySessionKind.CODEX_SESSION)) is not None
        bridge = await session.get(HttpBridgeSessionRecord, "retained-bridge")
        assert bridge is not None and bridge.state == HttpBridgeSessionState.ACTIVE
        assert bridge.account_id == stored.id


@pytest.mark.asyncio
@pytest.mark.parametrize("balance", [0.0, 17.25])
@pytest.mark.parametrize("empty_window_seconds", [None, 18000, 2592000])
async def test_secondary_only_snapshot_reaches_credit_consumers(
    _reset_db_state: bool, monkeypatch: pytest.MonkeyPatch, balance: float, empty_window_seconds: int | None
) -> None:
    payload = UsagePayload.model_validate(
        {
            "plan_type": "plus",
            "rate_limit": {
                "primary_window": None
                if empty_window_seconds is None
                else {"limit_window_seconds": empty_window_seconds},
                "secondary_window": {"used_percent": 80, "limit_window_seconds": 604800, "reset_at": 2100000000},
            },
            "credits": {"has_credits": balance > 0, "unlimited": False, "balance": str(balance)},
        }
    )

    async def fetch(**kwargs: object) -> UsagePayload:
        return payload

    monkeypatch.setattr(updater_module, "fetch_usage", fetch)
    async with SessionLocal() as session:
        account = _account()
        session.add(account)
        await session.commit()
        repo = UsageRepository(session)
        await repo.add_entry(
            account.id,
            100,
            window="primary",
            credits_has=True,
            credits_unlimited=False,
            credits_balance=99,
            recorded_at=utcnow() - timedelta(days=1),
        )
        result = await UsageUpdater(repo, AccountsRepository(session))._refresh_account(
            account, usage_account_id=account.chatgpt_account_id
        )
        assert result.fetch_succeeded and result.usage_written
        primary = await repo.latest_entry_for_account(account.id, window="primary")
        secondary = await repo.latest_entry_for_account(account.id, window="secondary")
        assert secondary is not None
        assert secondary.credits_balance == balance
        assert dashboard_credits(primary, secondary) == (balance > 0, False, balance)
        assert routing_credits(primary, secondary) == (balance > 0, False, balance)
