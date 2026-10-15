## MODIFIED Requirements

### Requirement: Background usage refresh reconciles recoverable blocked statuses

Background usage refresh SHALL reconcile persisted `rate_limited` and `quota_exceeded` accounts back to `active` after it writes fresh usage snapshots that prove the blocked window has recovered. This reconciliation SHALL be recovery-only and SHALL NOT promote `active` accounts into blocked statuses. For `rate_limited` accounts, recovery evidence SHALL come from the most recently recorded main-window row: when a post-block refresh no longer reports a short primary window and the last primary sample's own reset deadline has elapsed (or no primary sample exists), a fresh long-window row recorded after the block that still reports usage below `100%` proves recovery. While the last primary sample still claims an unexpired window (or omits reset metadata), or the newer long-window row is itself exhausted, primary freshness SHALL keep gating recovery.

A future persisted `reset_at` SHALL continue to block ordinary recovery except for a `rate_limited` Free account whose monthly usage history proves that the specific monthly window associated with the current block reset, or a `rate_limited` account whose long-window usage history proves the same for its blocked long window under the `account-routing` long-window exception. The monthly exception MUST require `blocked_at` and a future persisted `reset_at`, at least 30 seconds elapsed after `blocked_at`, a monthly baseline recorded strictly after `blocked_at` whose `reset_at` matches the persisted marker within five seconds, a real temporal reset in an adjacent monthly pair at or after that baseline, and both the transition's after sample and the latest monthly sample recorded after `blocked_at` with usage below `100%`. The reset pair MAY come from the current refresh or be selected from adjacent persisted post-block samples so recovery survives a process restart and tolerates sliding reset deadlines without comparing non-neighboring rows. The long-window exception MUST read persisted post-block history of the account's routing long window (the weekly secondary window, or the weekly window a weekly-only account reports in the primary slot; never a monthly window) and require a persisted deadline less than one long-window length after `blocked_at`, a baseline recorded strictly after `blocked_at` whose `reset_at` matches the persisted marker within five seconds, a real temporal reset in an adjacent pair at or after that baseline, and a latest long-window sample that still resets more than five seconds after the persisted marker. That proof MUST release the persisted deadline only into the 30-second minimum floor and the post-block primary and long-window evidence gate measured from `blocked_at`, so current primary exhaustion, an unexpired primary sample that predates the block, and an exhausted latest long window keep the account blocked. Availability without a matching anchored transition, reset timestamp jitter, an exhausted latest window, or monthly evidence for a non-Free account MUST NOT override the persisted cooldown.

Every recovery write MUST compare the current status, deactivation reason, `reset_at`, and `blocked_at`. A successful write SHALL set the account to `active` and clear the deactivation reason and both block markers. A compare-and-set miss MUST preserve the newer row and MUST NOT make the stale account snapshot eligible for warm-up.

#### Scenario: Scheduler recovers a stale rate-limited account from fresh primary usage
- **WHEN** an account is persisted as `rate_limited`
- **AND** the persisted rate-limit reset deadline has already elapsed
- **AND** a later background usage refresh writes a fresh primary usage row recorded after the persisted block marker
- **AND** that primary usage row reports usage below `100%`
- **THEN** the scheduler marks the account `active`
- **AND** it clears persisted `reset_at` and `blocked_at`

#### Scenario: Scheduler recovers a rate-limited account that never had a primary row
- **WHEN** an account is persisted as `rate_limited` with no stored primary-slot row at all
- **AND** the persisted rate-limit reset deadline has already elapsed
- **AND** a later background usage refresh records a fresh long-window row below `100%` after the persisted block marker
- **THEN** the scheduler marks the account `active`
- **AND** it clears persisted `reset_at` and `blocked_at`

#### Scenario: Scheduler recovers a rate-limited account when upstream stops reporting the primary window
- **WHEN** an account is persisted as `rate_limited`
- **AND** the persisted rate-limit reset deadline has already elapsed
- **AND** the last primary usage sample's own reset deadline has also elapsed
- **AND** a later background usage refresh records only a long-window usage row after the persisted block marker
- **AND** that long-window row reports usage below `100%`
- **THEN** the scheduler marks the account `active`
- **AND** it clears persisted `reset_at` and `blocked_at`

#### Scenario: Unexpired primary sample keeps gating recovery evidence
- **WHEN** an account is persisted as `rate_limited`
- **AND** the last primary usage sample predates the block but still claims an unexpired reset deadline
- **AND** a later refresh recorded only a fresh long-window row
- **AND** no qualifying reset-confirmed Free monthly transition matches the current block
- **THEN** the account stays `rate_limited` until fresh primary evidence arrives, the primary sample's reset deadline elapses, or a qualifying monthly reset is confirmed

#### Scenario: Scheduler recovers a legacy rate-limited account without a block marker
- **WHEN** an account is persisted as `rate_limited`
- **AND** the persisted rate-limit reset deadline has already elapsed
- **AND** the account has no persisted block marker
- **AND** a later background usage refresh writes a recent primary usage row that reports usage below `100%`
- **THEN** the scheduler marks the account `active`
- **AND** it clears persisted `reset_at`

#### Scenario: Scheduler preserves legacy rate-limited accounts without recent primary usage
- **WHEN** an account is persisted as `rate_limited`
- **AND** the persisted rate-limit reset deadline has already elapsed
- **AND** the account has no persisted block marker
- **AND** the latest primary usage row is not recent enough to prove background refresh recovery
- **AND** no newer long-window row proves a post-block refresh
- **THEN** the scheduler leaves the account `rate_limited`

#### Scenario: Scheduler preserves an unexpired rate-limit cooldown
- **WHEN** an account is persisted as `rate_limited`
- **AND** its persisted rate-limit reset deadline is still in the future
- **AND** a later background usage refresh writes fresh available usage
- **AND** no qualifying reset-confirmed Free monthly or long-window transition matches the current block
- **THEN** the scheduler leaves the account `rate_limited`

#### Scenario: Confirmed Free monthly reset recovers before a stale deadline
- **GIVEN** a Free account is persisted as `rate_limited` with `blocked_at` more than 30 seconds ago and a future `reset_at`
- **AND** a monthly baseline recorded strictly after `blocked_at` has a reset deadline within five seconds of the persisted marker
- **WHEN** background usage refresh confirms a real transition in an adjacent monthly pair at or after that matching baseline
- **AND** the transition's after sample and latest monthly sample were recorded after `blocked_at` and report usage below `100%`
- **THEN** the scheduler atomically marks the account `active` before the stale persisted deadline
- **AND** it clears `reset_at`, `blocked_at`, and the deactivation reason

#### Scenario: Persisted monthly transition recovers after scheduler restart
- **GIVEN** a qualifying Free monthly reset transition was persisted after `blocked_at`
- **AND** the scheduler process restarts after the transition is no longer the current in-memory before/after pair
- **WHEN** the restarted scheduler refreshes the still-`rate_limited` account before its stale persisted deadline
- **THEN** it may use a matching persisted baseline plus a later adjacent monthly transition pair as reset evidence
- **AND** it recovers the account through the same marker-guarded transition

#### Scenario: A baseline at the exact block timestamp cannot shadow a later valid baseline
- **GIVEN** persisted monthly history contains a reset-matching row recorded exactly at `blocked_at`
- **AND** a later row recorded strictly after `blocked_at` matches the same persisted reset marker
- **AND** an adjacent reset transition follows that later row
- **WHEN** the restarted scheduler resolves persisted recovery evidence
- **THEN** it MUST ignore the row recorded exactly at `blocked_at`
- **AND** it MUST use the later matching baseline to evaluate the qualifying transition

#### Scenario: An unanchored current transition cannot mask persisted recovery evidence
- **GIVEN** the current monthly before/after pair confirms a reset whose baseline does not match the blocked Free account's persisted reset marker
- **AND** persisted monthly history contains an eligible post-block baseline plus a qualifying adjacent reset transition
- **WHEN** the scheduler resolves monthly reset evidence
- **THEN** it MUST scan persisted history instead of short-circuiting on the unanchored current pair
- **AND** it MUST use evidence anchored to the persisted block marker for recovery and warm-up

#### Scenario: Minimum post-block floor prevents immediate recovery
- **GIVEN** a Free account was marked `rate_limited` less than 30 seconds ago
- **AND** monthly samples otherwise appear to prove a reset with available quota
- **WHEN** background usage refresh evaluates recovery
- **THEN** the account remains `rate_limited` with both block markers intact

#### Scenario: Mismatched monthly baseline does not recover the current block
- **GIVEN** a Free account has a future persisted rate-limit deadline
- **AND** monthly history contains a real reset transition whose baseline deadline differs from that marker by more than five seconds
- **WHEN** background usage refresh evaluates recovery
- **THEN** the transition is not treated as evidence for the current block
- **AND** the account remains `rate_limited`

#### Scenario: Later exhausted monthly state defeats older recovery evidence
- **GIVEN** a Free account has a qualifying post-block monthly reset transition whose after sample reports available quota
- **AND** its latest monthly sample reports usage at or above `100%`
- **WHEN** background usage refresh evaluates recovery
- **THEN** the account remains blocked

#### Scenario: Plus primary exhaustion is not released by monthly evidence
- **GIVEN** a Plus account is persisted as `rate_limited`
- **AND** its current primary usage reports `100%`
- **WHEN** background usage refresh observes available long-window usage or an unrelated reset transition
- **THEN** the account remains `rate_limited`

#### Scenario: Scheduler recovers a stale quota-exceeded account from fresh secondary usage
- **WHEN** an account is persisted as `quota_exceeded`
- **AND** a later background usage refresh writes a fresh secondary usage row that reports usage below `100%`
- **THEN** the scheduler marks the account `active`
- **AND** it clears persisted `reset_at` and `blocked_at`

#### Scenario: Scheduler does not tighten active accounts into blocked statuses
- **WHEN** background usage refresh evaluates an account currently persisted as `active`
- **THEN** the scheduler does not change that account to `rate_limited` or `quota_exceeded`

#### Scenario: Scheduler ignores stale pre-block recovery evidence
- **WHEN** an account is persisted as `rate_limited`
- **AND** the latest primary usage row was recorded before the persisted block marker
- **AND** no newer long-window row or qualifying post-block monthly reset transition proves recovery
- **THEN** the scheduler leaves the account blocked

#### Scenario: Scheduler skips recovery when the account row changed concurrently
- **WHEN** background usage refresh determines that a blocked account is recoverable
- **AND** the persisted account status, reason, or reset markers change before the scheduler writes recovery
- **THEN** the scheduler skips the stale recovery write
- **AND** warm-up does not use that stale recovery decision

#### Scenario: Scheduler clears stale deactivation reasons on recovery
- **WHEN** background usage refresh recovers a `rate_limited` or `quota_exceeded` account to `active`
- **THEN** the scheduler writes `deactivation_reason` as `NULL`

#### Scenario: Scheduler recovers a rate-limited account whose blocked long window reset
- **GIVEN** an account is persisted as `rate_limited` with `blocked_at` three hours ago and a future `reset_at` equal to its weekly reset
- **AND** a weekly row recorded strictly after `blocked_at` reports that reset within five seconds
- **WHEN** a later adjacent weekly pair shows usage dropping below `100%` while the window re-anchors between the two samples, or the previous reset crossing
- **AND** the latest weekly row still resets after the persisted marker and the fresh post-block primary row reports usage below `100%`
- **THEN** the scheduler marks the account `active` through the compare-and-set write and clears `reset_at` and `blocked_at`
- **AND** a compare-and-set miss leaves the newer row untouched

#### Scenario: Weekly-only account recovers from its primary-slot history
- **GIVEN** a weekly-only account reports its weekly window in the primary slot
- **WHEN** that primary-slot weekly history proves a confirmed reset of the window matching the persisted marker
- **THEN** the scheduler recovers the account exactly as it would from secondary-slot weekly history

#### Scenario: Unanchored long-window samples keep a running Retry-After cooldown
- **GIVEN** an account is persisted as `rate_limited` by a Retry-After hint 20 minutes in the future
- **WHEN** post-block weekly history only shows an unstarted window whose reset slides with each sample, a window that direct account use started after the block, or a real weekly reset whose baseline does not match the persisted deadline
- **THEN** the scheduler leaves the account `rate_limited` with both markers intact

#### Scenario: Long-window reset evidence must stay anchored and adjacent
- **GIVEN** an account is persisted as `rate_limited` with `blocked_at` and a future `reset_at`
- **WHEN** the only weekly row matching the persisted marker was recorded before or exactly at `blocked_at`, the persisted marker lies one long window or more after `blocked_at`, the reset moved without a confirmed adjacent transition, the latest weekly row reports the persisted marker again, fewer than 30 seconds have elapsed since `blocked_at`, or the current primary row is exhausted
- **THEN** the scheduler leaves the account `rate_limited`
