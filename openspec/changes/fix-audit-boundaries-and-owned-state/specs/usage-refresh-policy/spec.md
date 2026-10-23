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
Explicit usage refresh SHALL operate while routing remains held or the account is paused, including usable reauthentication-required accounts, without releasing the hold or replacing caller-owned status/policy. Fetch success MUST be distinct from relevant-window freshness. `refreshed` MUST require that operation to persist the requested window and, when the caller supplies `expectedResetAt`, that reset timestamp. The returned usage ID, recorded timestamp, reset timestamp and used percent MUST come from that persisted row; without a qualifying row none of them is returned. The upstream account identity polled MUST be kept as a value before the poll; if the account's upstream identity differs when the receipt would be returned, the request MUST conflict without a receipt.
#### Scenario: Partial or empty success does not imply freshness
- **WHEN** a successful fetch omits the requested window or returns a reset timestamp other than a supplied expectation
- **THEN** fetch success is reported but no relevant usage ID, recorded timestamp, reset timestamp or used percent is returned
#### Scenario: Failure cannot borrow cached freshness
- **WHEN** refresh fails
- **THEN** cached rows cannot supply a successful refresh receipt
#### Scenario: Hold remains active during fetch
- **WHEN** a held usable account is refreshed
- **THEN** routing remains blocked and a matching persisted window returns its exact receipt
#### Scenario: Refill reading for a paused preserve account
- **WHEN** a paused `preserve` account, held or not, is refreshed without `expectedResetAt` after its window refilled
- **THEN** the response proves the newly persisted window with its new reset timestamp and used percent
- **AND** the account stays paused with unchanged policy and hold
#### Scenario: Identity replaced during the poll
- **WHEN** the account's upstream identity is replaced while the poll runs and its status, policy and hold stay equal
- **THEN** the request conflicts and returns no receipt for the previous identity's reading

### Requirement: Untargeted usage reset consume refuses caller reset expectations
The dashboard usage-reset consume names no credit upstream. It MUST refuse a request carrying `expectedCreditId` or `expectedCreditExpiresAt` with a client error before credential, route or upstream work instead of ignoring the expectation. Requests without them SHALL retain the existing behavior.
#### Scenario: Approved target sent to the untargeted consume
- **WHEN** a caller supplies an expected reset credit identity or expiry to the usage-reset consume
- **THEN** the request is refused and no upstream consume is sent
