# Tasks

## 1. Recovery

- [x] 1.1 `long_window_reset_after_block` in `app/core/balancer/logic.py`: `RATE_LIMITED` only, plausible stored deadline, 30 s floor, non-monthly long window with reset and window-length metadata, finite usage, sample within the freshness horizon, window start after the block and not after the sample (60 s jitter), reset at least 60 s past the stored deadline.
- [x] 1.2 `_state_from_account` anchors the proof on the newest known block marker, lets it open the existing post-block evidence gate, and stops applying the runtime cooldown only when the account recovers.

## 2. Verification

- [x] 2.1 `tests/unit/test_load_balancer.py`: runtime (peer/restart and the marking replica holding a long Retry-After) and background recovery; negatives for stale, pre-block, unchanged reset, Retry-After throttle on an unchanged window, deadline beyond the new window, reset moved within jitter, window began within jitter of the block, unknown reset, unknown window length, window starting after the sample, implausible reset, malformed usage, exhausted new window, primary current exhaustion, primary exhaustion with unknown reset, post-429 floor, `QUOTA_EXCEEDED`, Free monthly sliding window, and a newer runtime block.
- [x] 2.2 `tests/unit/test_usage_refresh_scheduler_recovery.py`: reconcile writes `ACTIVE` through compare-and-set and leaves a concurrently changed row untouched.
- [x] 2.3 Red-walk on the original module, one production mutation per guard with exact-byte restore, focused files, `ruff check`, `ruff format --check`, `ty check`, `openspec validate --specs`, and strict change validation.
