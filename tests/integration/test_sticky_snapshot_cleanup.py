from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, StickySession, StickySessionKind
from app.db.session import SessionLocal
from app.modules.proxy.sticky_repository import StickySessionsRepository

pytestmark = pytest.mark.integration


async def _seed() -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        for account_id in ("snapshot-a", "snapshot-b"):
            session.add(
                Account(
                    id=account_id,
                    email="duplicate@example.invalid",
                    plan_type="plus",
                    access_token_encrypted=encryptor.encrypt("fixture-access"),
                    refresh_token_encrypted=encryptor.encrypt("fixture-refresh"),
                    id_token_encrypted=encryptor.encrypt("fixture-id"),
                    last_refresh=utcnow(),
                    status=AccountStatus.ACTIVE,
                )
            )
        await session.commit()
        repo = StickySessionsRepository(session)
        await repo.upsert("selected", "snapshot-a", kind=StickySessionKind.CODEX_SESSION)
        await repo.upsert("unchanged", "snapshot-a", kind=StickySessionKind.CODEX_SESSION)
        await repo.upsert("other-account", "snapshot-b", kind=StickySessionKind.CODEX_SESSION)


@pytest.mark.asyncio
async def test_snapshot_listing_uses_exact_account_id_despite_duplicate_email(async_client):
    await _seed()
    response = await async_client.get("/api/sticky-sessions", params={"accountId": "snapshot-a"})
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert {entry["key"] for entry in entries} == {"selected", "unchanged"}
    assert {entry["accountId"] for entry in entries} == {"snapshot-a"}
    assert all(entry["ownershipGeneration"] for entry in entries)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["rebind", "recreate"])
async def test_snapshot_delete_preserves_changed_owner_and_new_pins(async_client, change):
    await _seed()
    listing = await async_client.get("/api/sticky-sessions", params={"accountId": "snapshot-a"})
    entries = [entry for entry in listing.json()["entries"] if entry["key"] in ("selected", "unchanged")]
    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        if change == "recreate":
            await session.execute(delete(StickySession).where(StickySession.key == "selected"))
            await session.commit()
        await repo.upsert(
            "selected", "snapshot-b" if change == "rebind" else "snapshot-a", kind=StickySessionKind.CODEX_SESSION
        )
        await repo.upsert("new-after-list", "snapshot-a", kind=StickySessionKind.CODEX_SESSION)
    response = await async_client.post("/api/sticky-sessions/delete-snapshot", json={"sessions": entries})
    assert response.status_code == 200, response.text
    assert response.json()["deletedCount"] == 1
    assert response.json()["deleted"] == [{"key": "unchanged", "kind": "codex_session"}]
    assert response.json()["failed"] == [{"key": "selected", "kind": "codex_session", "reason": "changed_or_missing"}]
    async with SessionLocal() as session:
        remaining = (await session.execute(select(StickySession))).scalars().all()
        assert {row.key: row.account_id for row in remaining} == {
            "selected": "snapshot-b" if change == "rebind" else "snapshot-a",
            "other-account": "snapshot-b",
            "new-after-list": "snapshot-a",
        }


@pytest.mark.asyncio
async def test_snapshot_delete_checks_account_id_even_with_the_current_generation(async_client):
    await _seed()
    listing = await async_client.get("/api/sticky-sessions", params={"accountId": "snapshot-a"})
    entry = next(row for row in listing.json()["entries"] if row["key"] == "selected")
    entry["accountId"] = "snapshot-b"
    response = await async_client.post("/api/sticky-sessions/delete-snapshot", json={"sessions": [entry]})
    assert response.status_code == 200 and response.json()["deletedCount"] == 0
    async with SessionLocal() as session:
        row = await session.get(StickySession, ("selected", StickySessionKind.CODEX_SESSION))
        assert row is not None and row.account_id == "snapshot-a"
