## MODIFIED Requirements

### Requirement: Rate-limit cooldowns are enforced across replicas

A replica that did not observe the upstream 429 MUST NOT transition a `RATE_LIMITED` account to `ACTIVE` while the persisted `reset_at` deadline is in the future unless background usage refresh proves that the exact blocked Free monthly window reset under the strict exception below, or a post-block long-window sample proves a new window under the window-reset exception below. For `RATE_LIMITED` rows with `blocked_at` set but no persisted `reset_at` (legacy rows written before cooldown persistence), replicas MUST hold the account `RATE_LIMITED` until at least `blocked_at + RATE_LIMITED_MIN_COOLDOWN_SECONDS`. Recovery transitions MUST be written through the compare-and-set status update (`update_status_if_current`) so a stale snapshot cannot clobber a newer marking.

The reset-confirmed exception SHALL apply only to a Free account with a still-future persisted deadline after the 30-second minimum floor has elapsed. Post-block monthly history MUST contain a baseline whose reset deadline matches the persisted account deadline within five seconds, and an adjacent monthly before/after pair at or after that baseline MUST prove a real temporal reset. Both the after sample and latest monthly sample MUST be post-block and below `100%`. The recovery compare-and-set MUST match the persisted status, deactivation reason, `reset_at`, and `blocked_at`, then clear both markers when it writes `ACTIVE`. The evidence MAY be loaded from persisted history after a process restart, but availability alone and comparisons between non-neighboring rows MUST NOT satisfy the exception.

The window-reset exception SHALL apply on every replica and on the usage-refresh reconcile path to a `RATE_LIMITED` account with `blocked_at` and a plausible future persisted `reset_at`, once the 30-second minimum floor after the newest known block marker (persisted or this replica's runtime marker) has elapsed. The persisted `reset_at` does not record which window the 429 described, so the exception MUST rest on the latest applicable non-monthly long-window sample itself: the sample MUST be recorded within the usage freshness horizon, report finite usage below `100%`, carry `reset_at` and window-length metadata, describe a window whose start (`reset_at` minus the window length) is more than 60 seconds after the block marker and no more than 60 seconds after the sample was recorded, and reset at least 60 seconds after the persisted deadline. The primary window MUST independently pass the existing rate-limit evidence gate: an applicable primary sample MUST report less than `100%` and be recorded in a later integer second than the block, or its own reset MUST have elapsed with a newer available long-window sample. When the exception holds, the account MUST become `ACTIVE`, both block markers MUST clear through the compare-and-set update, and a replica holding a runtime cooldown for that block MUST stop applying it. Monthly windows keep only the strict Free reset-confirmed exception, `QUOTA_EXCEEDED` rows keep their own recovery rules, and reset credits, manual reactivation, or a successful upstream response MUST NOT count as window-reset evidence. Without the full proof, the original block semantics MUST hold.

This constraint applies to every recovery path that writes account status, including the usage-refresh reconcile path. A usage refresh that observes available quota for a `RATE_LIMITED` account with `blocked_at` set MUST NOT rewrite the account to `ACTIVE` or clear its markers while the effective persisted cooldown is running unless the strict reset-confirmed exception or the window-reset exception succeeds. The replica that observed the current 429 MAY still recover earlier through its runtime-cooldown-gated fresh-usage path only when its runtime block marker is at least as recent as the effective persisted `blocked_at`; leftover runtime state from an earlier 429 MUST NOT unlock early recovery of a newer block. `RATE_LIMITED` rows without `blocked_at` keep the existing fresh-usage recovery. Generic 429 and Retry-After cooldowns without matching reset evidence, reset timestamp jitter, exhausted post-reset windows, and non-Free account exhaustion MUST remain protected until their ordinary recovery condition is met.

#### Scenario: Usage refresh does not clear a running Retry-After cooldown

- **GIVEN** an account marked `RATE_LIMITED` by a 429 whose Retry-After hint persisted `reset_at` 20 minutes in the future and `blocked_at` set
- **WHEN** a periodic usage refresh fetches fresh usage showing available quota before that deadline
- **AND** no qualifying Free monthly reset transition matches the persisted deadline
- **AND** no fresh long-window sample reports a window that began after the block
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
- **AND** fresh usage reports availability but no temporal monthly reset whose baseline matches that deadline
- **AND** the latest long-window sample describes a window that began before the block
- **WHEN** any replica evaluates recovery
- **THEN** the account remains `RATE_LIMITED` until an ordinary recovery condition is met

#### Scenario: Peer replica does not flip a cooling account back

- **GIVEN** balancer instance A marked account X `RATE_LIMITED` from a 429 with no reset metadata
- **AND** account X's recorded usage is below 100%
- **WHEN** a second balancer instance sharing the same database runs account selection
- **THEN** account X is not selected
- **AND** the persisted row remains `RATE_LIMITED` with its `reset_at` deadline intact until the deadline elapses, strict reset-confirmed recovery succeeds, or the window-reset exception holds

#### Scenario: Stale runtime cooldown does not unlock early recovery of a newer block

- **GIVEN** a replica holds expired runtime cooldown state left over from an earlier 429 of account X
- **AND** account X was since re-marked `RATE_LIMITED` by a peer replica with a newer `blocked_at` and a future persisted `reset_at`
- **WHEN** the replica evaluates account X with usage recorded after the newer `blocked_at`
- **AND** no strict reset-confirmed transition matches the newer block
- **AND** no fresh long-window sample reports a window that began after the newer block
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
- **THEN** the Free monthly reset exception does not apply
- **AND** the account remains unavailable until its ordinary recovery condition is met

#### Scenario: Legacy row without reset_at is floored

- **GIVEN** a persisted `RATE_LIMITED` row with `blocked_at` five seconds ago and `reset_at` NULL
- **WHEN** a fresh balancer instance evaluates it during selection
- **THEN** the account stays `RATE_LIMITED` and is not selected
- **AND** once the 30-second floor has elapsed, recovery back to `ACTIVE` is permitted through the compare-and-set path

#### Scenario: Post-block long-window reset permits recovery on any replica

- **GIVEN** a Plus account was marked `RATE_LIMITED` three hours ago with a persisted deadline two days in the future
- **AND** a fresh weekly sample recorded after the block reports `3%` usage for a window that began ten minutes ago and resets after the persisted deadline
- **AND** the fresh primary sample recorded after the block reports `5%` usage
- **WHEN** a peer replica, a restarted replica, the replica that observed the 429, or background reconcile evaluates the account
- **THEN** the account becomes `ACTIVE` and selectable
- **AND** the compare-and-set update clears `reset_at` and `blocked_at`

#### Scenario: Retry-After throttle on an unchanged long window remains protected

- **GIVEN** an account has a persisted Retry-After deadline ten minutes in the future
- **AND** a fresh post-block weekly sample reports available usage for the window that was already running before the block
- **WHEN** any replica evaluates recovery
- **THEN** the account remains `RATE_LIMITED` with its persisted deadline

#### Scenario: Window-reset evidence must be fresh, unambiguous, and independent of primary exhaustion

- **GIVEN** a `RATE_LIMITED` account with `blocked_at` and a future persisted deadline
- **WHEN** the long-window sample is stale, predates the block, keeps or moves the deadline by less than 60 seconds, describes a window that began within 60 seconds of the block or after the sample, resets before the persisted deadline, lacks reset or window-length metadata, reports non-finite or exhausted usage, belongs to a monthly window, or the primary window is exhausted with a future or unknown reset
- **OR** the 30-second minimum floor after the newest block marker has not elapsed
- **THEN** the window-reset exception does not apply and the account remains `RATE_LIMITED` until another recovery condition is met
