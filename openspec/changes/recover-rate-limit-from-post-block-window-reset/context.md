# Context: recover-rate-limit-from-post-block-window-reset

## Purpose

Stop a stale 429 deadline from benching an account after upstream has actually reset the blocked long quota window. Protections for throttles, jitter, stale data, and primary exhaustion stay intact.

## Why the proof needs history

`UpstreamError` carries only `message`, `resets_at`, and `resets_in_seconds`. `handle_rate_limit` stores one deadline, so the persisted `reset_at` does not record whether it was a weekly reset, a primary reset, or a Retry-After throttle. A single latest sample cannot settle it either:

- An unstarted long window reports `reset_at` = sample time + window length. Every fresh sample therefore looks like a window that began after the block.
- Direct use of the account outside the balancer can start a window after an unrelated Retry-After 429. That window is anchored and post-block, but nothing reset.

The first version of this change trusted such a sample. On every replica and on background reconcile, it cleared a running Retry-After hold that had 20 minutes left (review P1). Persisted usage history is the only evidence that separates a real reset from these shapes. A post-block row whose reset matches the stored deadline ties the 429 to that window. A later adjacent pair accepted by `usage_reset_confirmed` (the previous reset crossed, or usage dropped while the window re-anchored between the two samples) proves a transition, which a sliding 0% window can never produce. The Free monthly exception already works this way, so the long-window exception reuses its helpers on the reconcile path. That path is the only one that loads post-block history.

## Why the selection path takes no reset proof

Request-time selection receives only the latest rows, so it cannot prove a reset. It keeps the original semantics: peers hold a stored deadline, and the observing replica recovers only after its own runtime cooldown elapses, using post-block evidence. Once reconcile writes the recovered row through compare-and-set, every replica and restarted process follows it. The first version also let a newer runtime block (one whose database write failed) end early on primary evidence recorded before that block (review P2). With the selection-path proof gone, that runtime cooldown holds as it did before. The observing replica's early-recovery gate, which the reconcile proof reuses, now measures evidence from the newest block marker it knows. After that runtime cooldown elapses, evidence recorded between the persisted and the newer marker no longer ends the block, for either the primary gate or the zero-primary path. In normal operation the runtime marker equals the persisted one in integer seconds, so nothing else changes.

## Example

At 07:57 on day 0 the weekly window is exhausted, and the 429 stores the weekly reset (day 2, 07:57) as `reset_at`. The post-429 refresh at 07:58 records `100%` with that reset, which becomes the baseline. On day 1 at 08:09, upstream resets weekly limits early. The 08:10 sample reports `0%` with a reset 7 days after a start between the two samples, and `usage_reset_confirmed` accepts the 08:09 → 08:10 pair. With a fresh post-block primary sample below `100%`, reconcile writes `ACTIVE` and clears both markers, and replicas route to the account. A Retry-After hold with 20 minutes left has no post-block weekly row whose reset matches its deadline, so it stays blocked whether the weekly window is unstarted, newly started, or even genuinely reset.

## Failure modes covered by pins

Unstarted sliding window, a window started by account use, a real reset without a baseline matching a Retry-After deadline, a baseline before or at the block instant, a deadline one window or more after the block (a sliding row can match it by coincidence), a reset moved without a confirmed transition, a latest sample back on the blocked deadline, the 30-second floor, primary exhaustion (current or unknown reset), an unexpired pre-block primary sample, a same-second primary sample, an exhausted long window, compare-and-set misses, and a newer unpersisted runtime block.

## Constants

- Five-second baseline match: `_BLOCK_RESET_MATCH_TOLERANCE_SECONDS`, shared with the Free monthly exception.
- 60-second minimum reset jump: `usage_reset_confirmed`, shared with limit warm-up.
- 30-second floor: `RATE_LIMITED_MIN_COOLDOWN_SECONDS`, unchanged.
