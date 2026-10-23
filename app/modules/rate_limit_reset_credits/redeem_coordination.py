"""Cross-replica coordination primitives for reset-credit redemption.

Two shared-database primitives back the redeem path:

- A durable idempotency ledger (``reset_credit_redeem_requests``) that pins the
  (account_id, redeem_request_id) pair to the credit selected on the first
  attempt, so a retry served by ANY replica retargets the same credit instead
  of burning a second one.
- A per-account claim row (``reset_credit_redeem_claims``) that serializes
  redemption across processes sharing one SQLite file via a single atomic
  conditional upsert with a lease the holder renews on a heartbeat while the
  redeem section runs. PostgreSQL keeps ``pg_advisory_xact_lock``.

All statements run on dedicated short-lived sessions committed immediately;
they never join the caller's transaction.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models import ResetCreditRedeemClaim, ResetCreditRedeemRequest
from app.db.session import SessionLocal, close_session

logger = logging.getLogger(__name__)

REDEEM_CLAIM_LEASE_SECONDS = 30.0
REDEEM_CLAIM_RENEW_INTERVAL_SECONDS = 10.0
REDEEM_CLAIM_RETRY_INTERVAL_SECONDS = 0.1
REDEEM_CLAIM_TIMEOUT_SECONDS = 15.0
REDEEM_REQUEST_TTL = timedelta(hours=24)


class RedeemClaimTimeoutError(Exception):
    """The per-account redeem claim stayed held past the acquisition timeout."""


class RedeemClaimLostError(Exception):
    """The holder can no longer prove an unexpired redemption claim."""


def new_redeem_claim_holder_id() -> str:
    return uuid.uuid4().hex


async def try_acquire_redeem_claim(
    account_id: str,
    holder_id: str,
    *,
    lease_seconds: float = REDEEM_CLAIM_LEASE_SECONDS,
) -> bool:
    """Attempt to claim the per-account redeem slot; True when claimed.

    Uses one atomic ``INSERT ... ON CONFLICT(account_id) DO UPDATE ... WHERE
    expires_at < now`` so only a missing or lease-expired claim can be taken —
    the same conditional-upsert shape as the scheduler-leader lease.
    """
    now = datetime.now(UTC)
    session = SessionLocal()
    try:
        insert_stmt = sqlite_insert(ResetCreditRedeemClaim).values(
            account_id=account_id,
            holder_id=holder_id,
            expires_at=now + timedelta(seconds=lease_seconds),
        )
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[ResetCreditRedeemClaim.account_id],
            set_={
                "holder_id": insert_stmt.excluded.holder_id,
                "expires_at": insert_stmt.excluded.expires_at,
            },
            where=ResetCreditRedeemClaim.expires_at < now,
        )
        result = await session.execute(stmt.returning(ResetCreditRedeemClaim.account_id))
        await session.commit()
        return result.scalar_one_or_none() is not None
    finally:
        await close_session(session)


async def acquire_redeem_claim(
    account_id: str,
    holder_id: str,
    *,
    lease_seconds: float = REDEEM_CLAIM_LEASE_SECONDS,
    retry_interval_seconds: float = REDEEM_CLAIM_RETRY_INTERVAL_SECONDS,
    timeout_seconds: float = REDEEM_CLAIM_TIMEOUT_SECONDS,
) -> None:
    """Acquire the per-account redeem claim, retrying until the timeout."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while True:
        if await try_acquire_redeem_claim(account_id, holder_id, lease_seconds=lease_seconds):
            return
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise RedeemClaimTimeoutError(
                f"reset-credit redeem claim for account {account_id} not acquired within {timeout_seconds}s"
            )
        await asyncio.sleep(min(retry_interval_seconds, remaining))


async def renew_redeem_claim(
    account_id: str,
    holder_id: str,
    *,
    lease_seconds: float = REDEEM_CLAIM_LEASE_SECONDS,
) -> bool:
    """Extend a still-live lease; never resurrect an expired holder."""
    session = SessionLocal()
    try:
        # A no-op write takes the database's writer/row lock and returns the
        # current deadline without extending it. Read the clock AFTER this
        # await: both a Python timestamp and database statement/transaction
        # NOW() can predate a lock wait. Concurrent heartbeat and admission
        # renewals serialize here instead of spuriously losing a read/CAS race.
        locked = await session.execute(
            update(ResetCreditRedeemClaim)
            .where(
                ResetCreditRedeemClaim.account_id == account_id,
                ResetCreditRedeemClaim.holder_id == holder_id,
            )
            .values(expires_at=ResetCreditRedeemClaim.expires_at)
            .returning(ResetCreditRedeemClaim.expires_at)
        )
        previous_expiry = locked.scalar_one_or_none()
        now = datetime.now(UTC)
        if previous_expiry is None:
            await session.rollback()
            return False
        # SQLite returns naive UTC DateTime values; preserve all microseconds.
        previous_expiry_utc = (
            previous_expiry.replace(tzinfo=UTC) if previous_expiry.tzinfo is None else previous_expiry.astimezone(UTC)
        )
        if previous_expiry_utc <= now:
            await session.rollback()
            return False
        expires_at = now + timedelta(seconds=lease_seconds)
        result = await session.execute(
            update(ResetCreditRedeemClaim)
            .where(
                ResetCreditRedeemClaim.account_id == account_id,
                ResetCreditRedeemClaim.holder_id == holder_id,
                ResetCreditRedeemClaim.expires_at == previous_expiry,
            )
            .values(expires_at=expires_at)
            .returning(ResetCreditRedeemClaim.account_id)
        )
        renewed = result.scalar_one_or_none() is not None
        # Keep the lock until this post-write check and commit/rollback. If
        # the extension awaited past the OLD deadline, none of its tentative
        # bytes become visible and neither heartbeat nor consume admission wins.
        checked_at = datetime.now(UTC)
        if not renewed or previous_expiry_utc <= checked_at or expires_at <= checked_at:
            await session.rollback()
            return False
        await session.commit()
    finally:
        await close_session(session)
    return renewed and expires_at > datetime.now(UTC)


async def renew_redeem_claim_periodically(
    account_id: str,
    holder_id: str,
    *,
    lease_seconds: float = REDEEM_CLAIM_LEASE_SECONDS,
    renew_interval_seconds: float = REDEEM_CLAIM_RENEW_INTERVAL_SECONDS,
) -> None:
    """Heartbeat that keeps a held claim's lease alive while the redeem section runs.

    Mirrors the scheduler-leader renew pattern: the holder extends
    ``expires_at`` every ``renew_interval_seconds`` (a fraction of the lease)
    so a legitimately slow redemption — usage fetch retries plus the upstream
    consume can exceed one lease — is not taken over by a second process.
    A failed or uncertain renewal raises to the owning task group, which stops
    the body. The caller cancels this task before releasing the exact holder.
    Cancellation of an in-flight external consume does not prove its outcome.
    """
    while True:
        await asyncio.sleep(renew_interval_seconds)
        try:
            renewed = await renew_redeem_claim(account_id, holder_id, lease_seconds=lease_seconds)
        except Exception as exc:
            raise RedeemClaimLostError("Reset-credit claim renewal could not establish ownership") from exc
        if not renewed:
            raise RedeemClaimLostError("Reset-credit claim expired or changed owner")


async def release_redeem_claim(account_id: str, holder_id: str) -> None:
    """Release the claim if this holder still owns it (lease expiry is the backstop)."""
    session = SessionLocal()
    try:
        await session.execute(
            delete(ResetCreditRedeemClaim).where(
                ResetCreditRedeemClaim.account_id == account_id,
                ResetCreditRedeemClaim.holder_id == holder_id,
            )
        )
        await session.commit()
    except Exception:
        logger.warning(
            "reset-credit redeem claim release failed account_id=%s (lease expiry will recover)",
            account_id,
            exc_info=True,
        )
    finally:
        await close_session(session)


async def get_pinned_redeem_credit_id(account_id: str, redeem_request_id: str) -> str | None:
    """Read the credit pinned to this redeem_request_id by any replica.

    Rows older than the 24h TTL are ignored (the read TTL matches the purge
    TTL applied by ``pin_redeem_request``), so an expired pin reads as absent.
    The caller then re-selects a fresh credit and re-pins it via
    ``pin_redeem_request`` instead of forwarding the redemption for a stale
    credit id that the purge path would otherwise drop.
    """
    now = datetime.now(UTC)
    session = SessionLocal()
    try:
        return await session.scalar(
            select(ResetCreditRedeemRequest.credit_id).where(
                ResetCreditRedeemRequest.account_id == account_id,
                ResetCreditRedeemRequest.redeem_request_id == redeem_request_id,
                ResetCreditRedeemRequest.created_at >= now - REDEEM_REQUEST_TTL,
            )
        )
    finally:
        await close_session(session)


async def pin_redeem_request(account_id: str, redeem_request_id: str, credit_id: str) -> str:
    """Durably pin the selected credit to this redeem request; first writer wins.

    Returns the authoritative credit id (the previously pinned one on
    conflict). Rows older than the 24h TTL for this account are purged in the
    same transaction BEFORE the insert, so a ``redeem_request_id`` reused after
    its prior row has aged past the TTL is re-pinned to the new credit instead
    of colliding with the stale row via ``ON CONFLICT DO NOTHING`` and losing
    the new pin.
    """
    now = datetime.now(UTC)
    session = SessionLocal()
    try:
        values = {
            "account_id": account_id,
            "redeem_request_id": redeem_request_id,
            "credit_id": credit_id,
            "created_at": now,
        }
        # Purge expired rows first: an expired row for the SAME
        # (account_id, redeem_request_id) would otherwise absorb the insert
        # below via ON CONFLICT DO NOTHING and then be deleted, leaving the new
        # attempt with no durable pin.
        await session.execute(
            delete(ResetCreditRedeemRequest).where(
                ResetCreditRedeemRequest.account_id == account_id,
                ResetCreditRedeemRequest.created_at < now - REDEEM_REQUEST_TTL,
            )
        )
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            await session.execute(
                pg_insert(ResetCreditRedeemRequest)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=[
                        ResetCreditRedeemRequest.account_id,
                        ResetCreditRedeemRequest.redeem_request_id,
                    ]
                )
            )
        else:
            await session.execute(
                sqlite_insert(ResetCreditRedeemRequest)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=[
                        ResetCreditRedeemRequest.account_id,
                        ResetCreditRedeemRequest.redeem_request_id,
                    ]
                )
            )
        await session.commit()
        stored = await session.scalar(
            select(ResetCreditRedeemRequest.credit_id).where(
                ResetCreditRedeemRequest.account_id == account_id,
                ResetCreditRedeemRequest.redeem_request_id == redeem_request_id,
            )
        )
        return stored if stored is not None else credit_id
    finally:
        await close_session(session)
