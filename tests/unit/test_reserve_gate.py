from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.balancer import logic
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.proxy import account_cache
from app.modules.proxy import load_balancer as lb
from tests.unit.test_proxy_load_balancer_refresh import (
    StubAccountsRepository,
    StubAdditionalUsageRepository,
    StubStickySessionsRepository,
    StubUsageRepository,
    _additional_entry,
    _repo_factory,
)

NOW = 2_000_000_000.0
RECORDED = datetime.fromtimestamp(NOW, timezone.utc).replace(tzinfo=None)


def account(aid: str = "reserve") -> Account:
    return Account(id=aid, email=f"{aid}@example.com", status=AccountStatus.ACTIVE, plan_type="pro")


def row(aid: str, used: float | None, window: str = "secondary", **credits) -> UsageHistory:
    return UsageHistory(
        account_id=aid,
        recorded_at=RECORDED,
        window=window,
        used_percent=used,
        reset_at=int(NOW) + 86400,
        window_minutes=10080 if window == "secondary" else 300,
        **credits,
    )


def build(*, secondary=95.0, primary=10.0, credits_has=True, credits_unlimited=False, credits_balance=10.0):
    credit = dict(credits_has=credits_has, credits_unlimited=credits_unlimited, credits_balance=credits_balance)
    return lb._state_from_account(
        account=account(),
        primary_entry=row("reserve", primary, "primary", **credit),
        secondary_entry=row("reserve", secondary, "secondary", **credit) if secondary is not None else None,
        runtime=lb.RuntimeState(),
        now=NOW,
    )


@pytest.mark.parametrize(("secondary", "primary"), [(95.0, 10.0), (100.0, 10.0), (10.0, 95.0), (None, 10.0)])
@pytest.mark.parametrize("bypass", [False, True])
def test_reserve_margin_excludes_real_states(secondary, primary, bypass):
    state = build(secondary=secondary, primary=primary)
    result = logic.select_account([state], now=NOW, bypass_quota_exceeded=bypass, ignore_standard_quota=bypass)
    assert result.account is None


@pytest.mark.parametrize(
    "credits",
    [
        dict(credits_has=True, credits_balance=None),
        dict(credits_has=None, credits_balance=1.0),
        dict(credits_has=None, credits_balance=None, credits_unlimited=True),
    ],
)
def test_each_positive_credit_signal_protects_reserve(credits):
    result = logic.select_account([build(**credits)], now=NOW)
    assert result.account is None


@pytest.mark.parametrize("secondary", [10.0, 94.99])
def test_standard_credit_traffic_under_margin_stays_available(secondary):
    result = logic.select_account([build(secondary=secondary)], now=NOW)
    assert result.account is not None


def test_no_stored_credits_does_not_add_a_reserve_gate():
    result = logic.select_account([build(credits_has=False, credits_balance=0.0)], now=NOW)
    assert result.account is not None


@pytest.mark.parametrize(
    "strategy",
    [
        "capacity_weighted",
        "usage_weighted",
        "fill_first",
        "round_robin",
        "sequential_drain",
        "reset_drain",
        "relative_availability",
    ],
)
def test_every_strategy_excludes_the_only_gated_account(strategy):
    gated = logic.AccountState("reserve", AccountStatus.ACTIVE, used_percent=0.0, capacity_credits=50400.0)
    gated.reserve_gated = True
    result = logic.select_account([gated], now=NOW, routing_strategy=strategy)
    assert result.account is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ignore_standard_quota": True},
        {"bypass_quota_exceeded": True},
        {"bypass_quota_exceeded_account_ids": {"reserve"}},
        {"traffic_class": logic.TRAFFIC_CLASS_OPPORTUNISTIC},
        {"recovery_probe_only": True},
        {"allow_backoff_fallback": True},
    ],
)
def test_reserve_gate_precedes_all_bypasses(kwargs):
    state = logic.AccountState("reserve", AccountStatus.ACTIVE, used_percent=1.0)
    if kwargs.get("recovery_probe_only"):
        state.health_tier = logic.HEALTH_TIER_PROBING
    state.reserve_gated = True
    assert logic.select_account([state], now=NOW, **kwargs).account is None


def test_quota_block_does_not_erase_last_known_credit_evidence():
    held = account()
    held.status = AccountStatus.QUOTA_EXCEEDED
    held.blocked_at = int(NOW) + 1
    held.reset_at = int(NOW) + 86400
    state = lb._state_from_account(
        account=held,
        primary_entry=None,
        secondary_entry=row(held.id, 99.0, credits_balance=10.0),
        runtime=lb.RuntimeState(),
        now=NOW + 2,
    )
    assert logic.select_account([state], now=NOW + 2, bypass_quota_exceeded=True).account is None


def test_standard_gate_fences_sessions_until_standard_recovery():
    aid = "session-reserve"
    a = account(aid)
    weekly = {aid: row(aid, 95.0, credits_balance=10.0)}
    lb._build_states(accounts=[a], latest_primary={}, latest_secondary=weekly, latest_monthly={}, runtime={}, now=NOW)
    assert account_cache.is_account_routing_unavailable(aid)
    lb._build_states(
        accounts=[a],
        latest_primary={},
        latest_secondary={},
        latest_monthly={},
        runtime={},
        now=NOW,
        ignore_standard_quota_account_ids=frozenset({aid}),
    )
    assert account_cache.is_account_routing_unavailable(aid)
    weekly[aid].used_percent = 10.0
    lb._build_states(accounts=[a], latest_primary={}, latest_secondary=weekly, latest_monthly={}, runtime={}, now=NOW)
    assert not account_cache.is_account_routing_unavailable(aid)


def test_standard_recovery_preserves_an_independent_pause_fence():
    aid = "paused-reserve"
    a = account(aid)
    account_cache.mark_account_routing_unavailable(aid)
    try:
        lb._build_states(
            accounts=[a],
            latest_primary={},
            latest_secondary={aid: row(aid, 10.0, credits_balance=10.0)},
            latest_monthly={},
            runtime={},
            now=NOW,
        )
        assert account_cache.is_account_routing_unavailable(aid)
    finally:
        account_cache.clear_account_routing_unavailable(aid)


def test_test_isolation_reset_clears_reserve_fences():
    aid = "reset-reserve"
    lb._build_states(
        accounts=[account(aid)],
        latest_primary={},
        latest_secondary={aid: row(aid, 95.0, credits_balance=10.0)},
        latest_monthly={},
        runtime={},
        now=NOW,
    )
    assert account_cache.is_account_routing_unavailable(aid)
    account_cache.clear_all_account_routing_unavailable()
    assert not account_cache.is_account_routing_unavailable(aid)


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["primary", "secondary", "monthly"])
async def test_additional_quota_excludes_credit_holders_before_replacing_usage(window, monkeypatch):
    async def overrides():
        return {}

    monkeypatch.setattr(lb, "_load_dashboard_additional_quota_routing_overrides", overrides)
    a = account(f"additional-reserve-{window}")
    recorded = datetime.now(timezone.utc).replace(tzinfo=None)
    standard = row(a.id, 10.0, window, credits_balance=10.0)
    usage = StubUsageRepository(
        primary={a.id: standard} if window == "primary" else {},
        secondary={a.id: standard} if window == "secondary" else {},
        monthly={a.id: standard} if window == "monthly" else {},
    )
    additional = StubAdditionalUsageRepository(
        primary={
            a.id: _additional_entry(
                1,
                account_id=a.id,
                window="primary",
                used_percent=1.0,
                recorded_at=recorded,
                reset_at=int(recorded.replace(tzinfo=timezone.utc).timestamp()) + 3600,
            )
        }
    )
    balancer = lb.LoadBalancer(
        lambda: _repo_factory(
            StubAccountsRepository([a]),
            usage,
            StubStickySessionsRepository(),
            additional,
        )
    )
    result = await balancer.select_account(additional_limit_name="codex_spark")
    assert result.account is None
