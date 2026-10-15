# Change: recover-rate-limit-from-post-block-window-reset

## Why

A stored 429 deadline overrides real recovery evidence. When upstream resets a long quota window early, a `RATE_LIMITED` account keeps its persisted `reset_at` (often days out): background reconcile seeds its throwaway runtime cooldown with that deadline, and peers or restarted processes hold no runtime evidence of the block, so the account stays benched even after usage history shows the blocked weekly window was replaced by a fresh one (Soju06/codex-lb#2404 recorded three Pro accounts stranded 3-4 days after a fleet-wide weekly reset; #2602 reports the same shape for weekly-only accounts). The only remedy is manual reactivation.

The first version of this change accepted one fresh sample whose window appeared to start after the block. Independent review refuted it: an unstarted weekly window reports a sliding reset (sample time plus the window length), and direct account use can start a window after an unrelated Retry-After 429, so a running throttle ended early on every replica and on background reconcile; the selection path also checked primary evidence against an older persisted block than this replica's newer runtime block.

## What Changes

- Background usage refresh gains a long-window reset-confirmed exception built from the existing Free monthly machinery. For a `RATE_LIMITED` account with `blocked_at` and a plausible future `reset_at`, it reads persisted post-block history of the routing long window (the weekly secondary window, or the weekly primary slot of a weekly-only account; never monthly) and requires: a deadline less than one long window after the block, a baseline recorded strictly after the block whose reset matches the deadline within five seconds, an adjacent pair at or after that baseline that `usage_reset_confirmed` accepts, and a latest sample that still resets more than five seconds after the deadline.
- The proof releases the stored deadline only into the 30-second floor and the observing replica's existing post-block evidence gate, measured from the persisted block. Current primary exhaustion, an unexpired primary sample that predates the block, a same-second sample, and an exhausted latest long window still hold the account. Recovery goes through the existing compare-and-set write, and every replica follows the recovered row.
- The selection path no longer reads a latest sample as reset proof, so no latest sample ends a stored deadline or a running runtime cooldown. The observing replica's early-recovery gate (primary and zero-primary paths) measures post-block evidence from the newest block marker it knows, so a newer 429 whose database write failed is not ended by usage recorded before it.
- Unchanged: the Free monthly strict exception and `QUOTA_EXCEEDED` rules. Reset credits, manual reactivation, and successful responses are still not evidence.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: MODIFIED "Rate-limit cooldowns are enforced across replicas" (long-window reset-confirmed exception and scenarios).
- `usage-refresh-policy`: MODIFIED "Background usage refresh reconciles recoverable blocked statuses" (the reconcile proof and its scenarios).

## Impact

- Code: `app/core/usage/refresh_scheduler.py` adds `_blocked_long_window_reset_confirmed`, which reuses `_latest_confirmed_reset_transition_after_baseline`, `usage_reset_confirmed`, the five-second match tolerance, and `usage_core.should_use_weekly_primary`, and passes its result to `background_recovery_state_from_account(blocked_window_reset=...)`. In `app/modules/proxy/load_balancer.py`, that flag replaces the seeded deadline with the post-429 floor, and the two observing-replica evidence checks use the newest known block marker (3019/3021 lines). `app/core/balancer/logic.py`, the usage updater, schema, and settings are untouched. There is one extra history read for each reconciled blocked account whose latest long-window sample already resets after its deadline.
- Operators: no new setting. Accounts stranded by an early upstream long-window reset return within a usage-refresh cycle without manual reactivation.
