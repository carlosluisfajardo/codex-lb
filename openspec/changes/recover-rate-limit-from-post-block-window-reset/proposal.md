# Change: recover-rate-limit-from-post-block-window-reset

## Why

A stored 429 deadline overrides real recovery evidence. When upstream resets a long quota window early, a `RATE_LIMITED` account keeps its persisted `reset_at` (often days out) on every replica: `_state_from_account` drops a plausible future deadline only on the replica that observed the 429 and only after that replica's runtime cooldown elapses, and `background_recovery_state_from_account` seeds the runtime cooldown with the persisted deadline itself. Peers, restarted processes, and background reconcile therefore keep the account benched even while fresh usage shows a new, nearly empty window (Soju06/codex-lb#2404 recorded three Pro accounts stranded 3-4 days after a fleet-wide weekly reset; #2602 reports the same shape for weekly-only accounts). The only remedy is manual reactivation.

## What Changes

- A `RATE_LIMITED` account with `blocked_at` and a plausible future persisted `reset_at` recovers on every replica and on the background reconcile path when the latest non-monthly long-window sample proves a new window: recorded within the freshness horizon, finite usage below `100%`, reset and window-length metadata present, window start (`reset_at` minus window length) more than 60 s after the newest known block marker and no more than 60 s after the sample, and reset at least 60 s past the stored deadline. The 30 s post-429 floor still applies.
- The proof only opens the existing post-block evidence gate (`_rate_limited_freshness_entry` plus the strict-second post-block check) that the observing replica already uses, so an exhausted long window, a current primary exhaustion, or a primary exhaustion with unknown reset still holds the block.
- When the account recovers through the proof, the state build also stops applying that replica's runtime cooldown for the block; persistence still goes through the existing compare-and-set writes.
- Unchanged: stale, pre-block, same-second, jittered, unknown, or malformed samples; deadlines beyond the new window; monthly windows (the strict Free exception); `QUOTA_EXCEEDED`; credits, manual reactivation, and successful responses are not evidence.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: MODIFIED "Rate-limit cooldowns are enforced across replicas" (window-reset exception and scenarios).
- `usage-refresh-policy`: MODIFIED "Background usage refresh reconciles recoverable blocked statuses" (the reconcile path admits the same exception).

## Impact

- Code: `app/core/balancer/logic.py` gains the pure `long_window_reset_after_block` predicate (the `proxy-architecture` ceiling leaves `load_balancer.py` only 8 spare lines); `app/modules/proxy/load_balancer.py` adds one import and a five-line hook in `_state_from_account` (3018/3021 lines). `refresh_scheduler.py`, the usage updater, schema, and settings are untouched.
- Operators: no new setting; accounts stranded by an early upstream long-window reset return without manual reactivation.
