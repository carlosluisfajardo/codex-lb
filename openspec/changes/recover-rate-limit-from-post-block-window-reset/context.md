# Context: recover-rate-limit-from-post-block-window-reset

## Purpose

Stop a stale 429 deadline from benching an account after upstream has already started a new long quota window, without weakening the protections for throttles, jitter, stale data, or primary exhaustion.

## Why the proof ignores which window the deadline came from

`UpstreamError` carries only `message`, `resets_at`, and `resets_in_seconds`; `handle_rate_limit` stores one deadline. The persisted `reset_at` therefore does not record whether it was a weekly reset, a primary reset, or a Retry-After throttle. Comparing a sample's reset to that deadline ("did it move?") would be a guess: a 20-minute throttle or a 5-hour primary block always "differs" from the weekly reset. The proof instead asks the sample itself: did its window begin after the block? A window that was already running at the block can never satisfy it, so throttles and primary blocks on an unchanged long window keep their original semantics. Requiring the new window to end past the stored deadline keeps longer holds intact.

## Example

Blocked at 07:57 on day 0 with a stored deadline on day 5. On day 2 upstream resets the weekly window; the 08:10 sample reports `0%` with `reset_at` = 08:10 on day 9 and a 10080-minute window. Window start is 08:10 on day 2 (after the block, not after the sample) and day 9 is past day 5, so with a non-exhausted primary the account becomes `ACTIVE` on every replica and on background reconcile. These are the row shapes recorded in Soju06/codex-lb#2404.

## Known limitation

An unstarted long window reports a sliding reset (`recorded_at` + window length). After a Retry-After throttle on an account whose long window had not started, the next fresh sample therefore looks like a new window and the throttle can end early. With only the latest sample and a deadline that does not name its window, that case is indistinguishable from the early-reset shape above. A history-anchored proof (a pre-reset sample whose reset matches the stored deadline, as the Free monthly exception does) would separate them but needs reconcile-side history outside this change. A throttle that persists re-marks the account on its next 429.

## Constants

- 60 s jitter: matches the limit warm-up threshold for a real reset jump; smaller reset differences are timestamp jitter.
- 30 s floor: `RATE_LIMITED_MIN_COOLDOWN_SECONDS`, unchanged.
