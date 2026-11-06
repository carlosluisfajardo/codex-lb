from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone

from app.core import usage as usage_core
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, UsageHistory


def apply_usage_quota(
    *,
    status: AccountStatus,
    primary_used: float | None,
    primary_reset: int | None,
    primary_window_minutes: int | None,
    runtime_reset: float | None,
    secondary_used: float | None,
    secondary_reset: int | None,
    credits_has: bool | None = None,
    credits_unlimited: bool | None = None,
    credits_balance: float | None = None,
    infer_status_from_usage: bool = True,
    now: float | None = None,
) -> tuple[AccountStatus, float | None, float | None]:
    now = time.time() if now is None else now
    used_percent = primary_used
    reset_at = runtime_reset

    if status in (AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED, AccountStatus.PAUSED):
        return status, used_percent, reset_at

    has_credit_override = _has_credit_override(
        credits_has=credits_has,
        credits_unlimited=credits_unlimited,
        credits_balance=credits_balance,
    )
    if secondary_used is not None:
        if secondary_used >= 100.0:
            if has_credit_override:
                if status == AccountStatus.QUOTA_EXCEEDED:
                    status = AccountStatus.ACTIVE
                    reset_at = None
            else:
                used_percent = 100.0
                if status == AccountStatus.QUOTA_EXCEEDED or infer_status_from_usage:
                    if secondary_reset is not None:
                        reset_at = secondary_reset
                    elif reset_at is not None and reset_at <= now:
                        # Exhaustion without a reset cannot expire through an
                        # older fallback deadline in foreground selection.
                        reset_at = None
                    status = AccountStatus.QUOTA_EXCEEDED
                    return status, used_percent, reset_at
        if status == AccountStatus.QUOTA_EXCEEDED:
            if runtime_reset and runtime_reset > now:
                reset_at = runtime_reset
            else:
                status = AccountStatus.ACTIVE
                reset_at = None
    elif status == AccountStatus.QUOTA_EXCEEDED and secondary_reset is not None and infer_status_from_usage:
        reset_at = secondary_reset

    if has_credit_override and status == AccountStatus.QUOTA_EXCEEDED:
        primary_exhausted = primary_used is not None and primary_used >= 100.0
        if not primary_exhausted:
            status = AccountStatus.ACTIVE
            reset_at = None

    if primary_used is not None:
        if primary_used >= 100.0:
            used_percent = 100.0
            if infer_status_from_usage:
                if primary_reset is not None:
                    reset_at = primary_reset
                else:
                    reset_at = _fallback_primary_reset(primary_window_minutes, now=now) or reset_at
                status = AccountStatus.RATE_LIMITED
                return status, used_percent, reset_at
        if status == AccountStatus.RATE_LIMITED:
            if runtime_reset and runtime_reset > now:
                reset_at = runtime_reset
            else:
                status = AccountStatus.ACTIVE
                reset_at = None
    elif status == AccountStatus.RATE_LIMITED and secondary_used is not None and secondary_used < 100.0:
        # No primary data at all — upstream stopped reporting the short
        # window. The long-window sample proves availability, so the block
        # clears once its runtime reset elapses instead of pinning the
        # account rate-limited indefinitely. Callers that cannot tie the
        # sample to a reset deadline or post-block evidence must preserve
        # the block themselves.
        if runtime_reset and runtime_reset > now:
            reset_at = runtime_reset
        else:
            status = AccountStatus.ACTIVE
            reset_at = None

    return status, used_percent, reset_at


def _fallback_primary_reset(primary_window_minutes: int | None, *, now: float) -> float | None:
    window_minutes = primary_window_minutes or usage_core.default_window_minutes("primary")
    if not window_minutes:
        return None
    return now + float(window_minutes) * 60.0


def _has_credit_override(
    *,
    credits_has: bool | None,
    credits_unlimited: bool | None,
    credits_balance: float | None,
) -> bool:
    return _has_usable_credits(
        credits_has=credits_has,
        credits_unlimited=credits_unlimited,
        credits_balance=credits_balance,
    )


def _has_usable_credits(
    *,
    credits_has: bool | None,
    credits_unlimited: bool | None,
    credits_balance: float | None,
) -> bool:
    if credits_unlimited is True:
        return True
    if credits_balance is None:
        return False
    try:
        return float(credits_balance) > 0.0
    except (TypeError, ValueError):
        return False


RESERVE_GATE_USED_PERCENT = 95.0


def holds_reserve_credits(
    *, credits_has: bool | None, credits_unlimited: bool | None, credits_balance: float | None
) -> bool:
    """A positive credit signal protects the reserve even when no numeric balance is supplied."""
    return credits_has is True or _has_usable_credits(
        credits_has=credits_has, credits_unlimited=credits_unlimited, credits_balance=credits_balance
    )


def reserve_gate_applies(
    *,
    credits_has: bool | None,
    credits_unlimited: bool | None,
    credits_balance: float | None,
    secondary_used_percent: float | None,
    primary_used_percent: float | None,
) -> bool:
    """Keep stored credits in reserve before either plan window is exhausted."""
    if not holds_reserve_credits(
        credits_has=credits_has, credits_unlimited=credits_unlimited, credits_balance=credits_balance
    ):
        return False
    return (
        secondary_used_percent is None
        or secondary_used_percent >= RESERVE_GATE_USED_PERCENT
        or (primary_used_percent is not None and primary_used_percent >= RESERVE_GATE_USED_PERCENT)
    )


def usage_entry_recorded_after_block(entry: UsageHistory | AdditionalUsageHistory | None, blocked_at: float) -> bool:
    if entry is None or entry.recorded_at is None:
        return False
    recorded_at = entry.recorded_at
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)
    # Persistence truncates block timestamps to whole seconds. A sample
    # within that same second cannot prove it was captured after the block.
    return int(recorded_at.timestamp()) > int(blocked_at)


def reserve_account_ids(
    accounts: Iterable[Account],
    *usage_windows: Mapping[str, UsageHistory | AdditionalUsageHistory],
) -> frozenset[str]:
    reserve: set[str] = set()
    for account in accounts:
        credits_has, credits_unlimited, credits_balance = extract_credit_status(
            *(window.get(account.id) for window in usage_windows)
        )
        if holds_reserve_credits(
            credits_has=credits_has, credits_unlimited=credits_unlimited, credits_balance=credits_balance
        ):
            reserve.add(account.id)
    return frozenset(reserve)


def extract_credit_status(
    *entries: UsageHistory | AdditionalUsageHistory | None,
    recorded_after: float | None = None,
) -> tuple[bool | None, bool | None, float | None]:
    credit_entries: list[UsageHistory] = [
        entry
        for entry in entries
        if isinstance(entry, UsageHistory)
        and (recorded_after is None or usage_entry_recorded_after_block(entry, recorded_after))
        and not (entry.credits_has is None and entry.credits_unlimited is None and entry.credits_balance is None)
    ]
    if not credit_entries:
        return None, None, None
    entry = max(
        credit_entries,
        key=lambda item: item.recorded_at if item.recorded_at is not None else datetime.min,
    )
    if entry is not None:
        return entry.credits_has, entry.credits_unlimited, entry.credits_balance
    return None, None, None
