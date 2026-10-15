## MODIFIED Requirements

### Requirement: Rate-limit cooldowns are enforced across replicas

A replica that did not observe the upstream 429 MUST NOT transition a `RATE_LIMITED` account to `ACTIVE` while the persisted `reset_at` deadline is in the future unless background usage refresh proves that the exact blocked Free monthly window reset under the strict exception below, or that the exact blocked long window reset under the long-window exception below. For `RATE_LIMITED` rows with `blocked_at` set but no persisted `reset_at` (legacy rows written before cooldown persistence), replicas MUST hold the account `RATE_LIMITED` until at least `blocked_at + RATE_LIMITED_MIN_COOLDOWN_SECONDS`. Recovery transitions MUST be written through the compare-and-set status update (`update_status_if_current`) so a stale snapshot cannot clobber a newer marking.

The reset-confirmed exception SHALL apply only to a Free account with a still-future persisted deadline after the 30-second minimum floor has elapsed. Post-block monthly history MUST contain a baseline whose reset deadline matches the persisted account deadline within five seconds, and an adjacent monthly before/after pair at or after that baseline MUST prove a real temporal reset. Both the after sample and latest monthly sample MUST be post-block and below `100%`. The recovery compare-and-set MUST match the persisted status, deactivation reason, `reset_at`, and `blocked_at`, then clear both markers when it writes `ACTIVE`. The evidence MAY be loaded from persisted history after a process restart, but availability alone and comparisons between non-neighboring rows MUST NOT satisfy the exception.

The long-window exception SHALL apply only on the usage-refresh reconcile path to a `RATE_LIMITED` account with `blocked_at` and a plausible future persisted `reset_at` whose routing long window is not monthly: the weekly secondary window, or the weekly window that a weekly-only account reports in the primary slot. The persisted `reset_at` does not record which window the 429 described, and an unstarted long window reports a sliding reset, so no single sample MAY satisfy the exception, however fresh, available, or recently started its window is. The persisted deadline MUST be less than one long-window length after `blocked_at`, because only a window already running at the block can be the window the deadline names. Post-block history of that long window MUST contain a baseline recorded strictly after `blocked_at` whose reset deadline matches the persisted deadline within five seconds, an adjacent before/after pair at or after that baseline MUST prove a real temporal reset, and the latest long-window sample MUST still reset more than five seconds after the persisted deadline. A proven reset MUST replace the persisted deadline only with the 30-second minimum floor and the post-block evidence gate that the observing replica uses, measured from the persisted `blocked_at`: an applicable primary sample MUST report less than `100%` and be recorded in a later integer second than the block, or its own reset MUST have elapsed with a newer available long-window sample, and the latest long-window sample MUST report less than `100%`. Recovery MUST be written through the compare-and-set update and clear both markers; every replica then follows the recovered row. Reset credits, manual reactivation, a successful upstream response, availability alone, and comparisons between non-neighboring rows MUST NOT count as evidence. Without the full proof, the original block semantics MUST hold.

This constraint applies to every recovery path that writes account status, including the usage-refresh reconcile path. A usage refresh that observes available quota for a `RATE_LIMITED` account with `blocked_at` set MUST NOT rewrite the account to `ACTIVE` or clear its markers while the effective persisted cooldown is running unless the Free monthly or long-window reset-confirmed exception succeeds. The replica that observed the current 429 MAY still recover earlier through its runtime-cooldown-gated fresh-usage path only when its runtime block marker is at least as recent as the effective persisted `blocked_at`, and only from evidence recorded in a later integer second than the newest block marker it knows, including a runtime marker whose database write failed; leftover runtime state from an earlier 429 MUST NOT unlock early recovery of a newer block, and a running runtime cooldown MUST NOT end because a latest sample looks like a new long window. `RATE_LIMITED` rows without `blocked_at` keep the existing fresh-usage recovery. Generic 429 and Retry-After cooldowns without matching reset evidence, reset timestamp jitter, exhausted post-reset windows, and non-Free account exhaustion MUST remain protected until their ordinary recovery condition is met.

#### Scenario: Usage refresh does not clear a running Retry-After cooldown

- **GIVEN** an account marked `RATE_LIMITED` by a 429 whose Retry-After hint persisted `reset_at` 20 minutes in the future and `blocked_at` set
- **WHEN** a periodic usage refresh fetches fresh usage showing available quota before that deadline
- **AND** no qualifying Free monthly or long-window reset transition matches the persisted deadline
- **THEN** the persisted row keeps status `RATE_LIMITED` with its `reset_at` and `blocked_at` intact
- **AND** once the deadline elapses, a later refresh may recover the account to `ACTIVE` through the compare-and-set path

#### Scenario: Confirmed blocked Free monthly reset permits peer recovery

- **GIVEN** replica A marked a Free account `RATE_LIMITED` with `blocked_at` and a persisted deadline matching that account's monthly window
- **AND** the 30-second minimum floor has elapsed
- **WHEN** replica B observes a real post-block transition from the matching monthly baseline into a new available monthly window
- **AND** the latest monthly sample remains below `100%`
- **THEN** replica B may compare-and-set the account to `ACTIVE` before the old persisted deadline
- **AND** a successful transition clears `reset_at` and `blocked_at`

#### Scenario: Generic 429 without matching reset evidence remains protected

- **GIVEN** an account has a future persisted cooldown from an upstream 429 or Retry-After hint
- **AND** fresh usage reports availability but no temporal monthly or long-window reset whose baseline matches that deadline
- **WHEN** any replica evaluates recovery
- **THEN** the account remains `RATE_LIMITED` until an ordinary recovery condition is met

#### Scenario: Peer replica does not flip a cooling account back

- **GIVEN** balancer instance A marked account X `RATE_LIMITED` from a 429 with no reset metadata
- **AND** account X's recorded usage is below 100%
- **WHEN** a second balancer instance sharing the same database runs account selection
- **THEN** account X is not selected
- **AND** the persisted row remains `RATE_LIMITED` with its `reset_at` deadline intact until the deadline elapses or strict reset-confirmed recovery succeeds

#### Scenario: Stale runtime cooldown does not unlock early recovery of a newer block

- **GIVEN** a replica holds expired runtime cooldown state left over from an earlier 429 of account X
- **AND** account X was since re-marked `RATE_LIMITED` by a peer replica with a newer `blocked_at` and a future persisted `reset_at`
- **WHEN** the replica evaluates account X with usage recorded after the newer `blocked_at`
- **AND** no strict reset-confirmed transition matches the newer block
- **THEN** account X stays `RATE_LIMITED` and is not selected until the persisted deadline elapses

#### Scenario: Concurrent newer block wins the recovery race

- **GIVEN** reset evidence qualifies a blocked Free account for early recovery
- **AND** another replica changes its status or either block marker before recovery commits
- **WHEN** the recovery compare-and-set evaluates the older snapshot
- **THEN** it does not overwrite the newer account row
- **AND** the account is not made routable from the stale evidence

#### Scenario: Exhausted Plus primary window remains protected

- **GIVEN** a Plus account is `RATE_LIMITED` with primary usage at `100%`
- **WHEN** a replica observes available long-window usage or a long-window reset
- **THEN** neither reset-confirmed exception makes the account available
- **AND** the account remains unavailable until its ordinary recovery condition is met

#### Scenario: Legacy row without reset_at is floored

- **GIVEN** a persisted `RATE_LIMITED` row with `blocked_at` five seconds ago and `reset_at` NULL
- **WHEN** a fresh balancer instance evaluates it during selection
- **THEN** the account stays `RATE_LIMITED` and is not selected
- **AND** once the 30-second floor has elapsed, recovery back to `ACTIVE` is permitted through the compare-and-set path

#### Scenario: Confirmed blocked long-window reset recovers the account for every replica

- **GIVEN** a Plus account was marked `RATE_LIMITED` three hours ago with a persisted deadline equal to its weekly reset two days away
- **AND** post-block weekly samples report that reset until an adjacent pair shows usage dropping to `3%` while the window re-anchors between the two samples
- **AND** the fresh primary sample recorded after the block reports `5%` usage
- **WHEN** background usage refresh evaluates the account, including after a process restart
- **THEN** the compare-and-set update makes the account `ACTIVE` and clears `reset_at` and `blocked_at`
- **AND** peer and restarted replicas route to the recovered row

#### Scenario: A latest long-window sample does not end an unrelated Retry-After cooldown

- **GIVEN** an account has a persisted Retry-After deadline 20 minutes in the future
- **AND** its fresh post-block weekly sample reports an unstarted window whose reset slides with the sample time, or a window that direct account use started after the block
- **WHEN** any replica, the replica holding the runtime cooldown, or background usage refresh evaluates recovery
- **THEN** the account remains `RATE_LIMITED` with its persisted deadline, `blocked_at`, and any running runtime cooldown

#### Scenario: A newer unpersisted runtime block needs evidence recorded after it

- **GIVEN** a replica holds runtime state for a newer 429 whose database write failed, so the persisted `blocked_at` is older
- **AND** its fresh weekly sample reports a window that began after both markers, while its available primary sample (or, for a zero-primary plan, its available long-window sample) was recorded between them
- **WHEN** that replica evaluates the account while its runtime cooldown is running or after it elapsed
- **THEN** the account stays `RATE_LIMITED` with its persisted deadline until evidence recorded after the newer marker or another ordinary recovery condition ends the block

#### Scenario: Long-window reset evidence must be anchored to the blocked window

- **GIVEN** a `RATE_LIMITED` account with `blocked_at` and a future persisted deadline
- **WHEN** no post-block weekly baseline matches that deadline, the only matching baseline predates or coincides with the block, the deadline lies one long window or more after the block, the reset moves without a confirmed adjacent transition, the latest sample reports the blocked deadline again or exhausted usage, the primary window is exhausted with a future or unknown reset, the long window is monthly, or the 30-second minimum floor has not elapsed
- **THEN** the long-window exception does not apply and the account remains `RATE_LIMITED` until another recovery condition is met
