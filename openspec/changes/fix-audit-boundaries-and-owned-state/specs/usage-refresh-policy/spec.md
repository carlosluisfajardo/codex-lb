## ADDED Requirements

### Requirement: Usage errors compare the failed credential generation
Status-changing usage errors MUST compare the state fields and refresh-token ciphertext captured before the failed request. Retried requests MUST capture their own generation. A stale error MUST NOT downgrade repaired authentication or perform sticky and bridge deactivation side effects.
#### Scenario: Repair wins against delayed failure
- **WHEN** reauthentication rotates credentials during an older pending usage request
- **THEN** its delayed permanent error cannot downgrade the repaired account or its ownership state

### Requirement: Secondary-only snapshots retain credit evidence
A writable secondary window MUST carry fetched credit fields when no writable primary or monthly row carries them, including explicit zero. Dashboard and routing consumers MUST use the fresh evidence.
#### Scenario: Zero replaces stale positive evidence
- **WHEN** a secondary-only fetch reports zero credits
- **THEN** consumers observe the new zero instead of older positive primary evidence

### Requirement: Held refresh identifies the persisted requested window
Explicit usage refresh SHALL operate while routing remains held, including usable reauthentication-required accounts, without releasing the hold or replacing caller-owned status/policy. Fetch success MUST be distinct from relevant-window freshness. `refreshed` MUST require that operation to persist the requested window with the expected reset timestamp; its usage ID and recorded timestamp MUST come from that row.
#### Scenario: Partial or empty success does not imply freshness
- **WHEN** a successful fetch omits the requested window or returns another reset timestamp
- **THEN** fetch success is reported but no relevant usage ID or recorded timestamp is returned
#### Scenario: Failure cannot borrow cached freshness
- **WHEN** refresh fails
- **THEN** cached rows cannot supply a successful refresh receipt
#### Scenario: Hold remains active during fetch
- **WHEN** a held usable account is refreshed
- **THEN** routing remains blocked and a matching persisted window returns its exact receipt
