## ADDED Requirements

### Requirement: Routing holds preserve authentication and policy
Accounts SHALL expose a persisted `routingHeld` independent of status and manual policy. A hold MUST exclude every otherwise-routable state, including usable `reauth_required`, active and recoverable quota states. Replica availability caches MUST preserve holds across refresh and ordinary reauthentication cache clearing. Releasing a hold MUST NOT replace status or policy.
#### Scenario: Hold usable reauthentication state
- **WHEN** a caller conditionally holds a usable reauthentication-required account
- **THEN** routing excludes it and its authentication status remains unchanged
#### Scenario: Replica refresh preserves the hold
- **WHEN** a peer refreshes availability and clears an ordinary authentication-unavailable marker
- **THEN** the persisted hold still prevents routing
#### Scenario: An older read finishes after a completed hold
- **WHEN** an older availability read resumes after the hold endpoint has published its newer snapshot and returned
- **THEN** the older read cannot overwrite that snapshot
- **AND** ordinary reauthentication clearing still leaves bridge reuse blocked until the hold is released
#### Scenario: A newer refresh is still pending when the hold completes
- **WHEN** the hold endpoint finishes its read while a later refresh remains pending or subsequently fails
- **THEN** the successful hold observation is published and ordinary reauthentication clearing cannot permit bridge reuse
#### Scenario: Reset invalidates an unfinished read
- **WHEN** a refresh resumes after the cache was reset
- **THEN** it cannot republish the pre-reset snapshot

### Requirement: Routing writes compare caller expectations atomically
Hold writes MUST require expected status, policy and hold state. Policy, pause and reactivate writes SHALL accept that expectation while retaining legacy requests without it. Supplied expectations MUST be checked in the mutation transaction; mismatch MUST conflict without overwriting current state.
#### Scenario: Human policy changes after request validation
- **WHEN** another writer changes policy before the final conditional write
- **THEN** the request conflicts and preserves intervening status, policy and hold
#### Scenario: Matching expectation permits the action
- **WHEN** the persisted tuple matches the caller expectation
- **THEN** the requested allowed transition succeeds

### Requirement: Delayed selection preserves newer runtime state
Sticky selection MUST synchronize each runtime only against its observed version, including unselected accounts. Its own lease acquisition MUST NOT invalidate otherwise-current recovery synchronization.
#### Scenario: Sibling error arrives during selection
- **WHEN** another task records an unselected sibling error before synchronization
- **THEN** the newer error count, timestamp and cooldown survive
#### Scenario: Selection acquires its own lease
- **WHEN** selection recovers expired backoff and acquires a lease
- **THEN** both the recovered runtime state and lease are retained

### Requirement: Empty pools retain continuity eligibility
An empty selectable pool MUST retain model- and caller-scope-qualified continuity candidates, distinguish unavailability from policy exclusion, and keep unavailable accounts unroutable.
#### Scenario: Paused but eligible owner
- **WHEN** the only owner is paused but satisfies model and scope
- **THEN** selection reports unavailability rather than policy conflict and preserves the pin
