## ADDED Requirements

### Requirement: SQLite lease loss stops the owning redemption
Expired claims MUST NOT be renewed into ownership. Failed or uncertain renewal MUST stop the owning body. Dashboard and v1 consume MUST re-establish current claim ownership immediately before admission. Cleanup MUST cover durable acquisition before return, release only the exact holder and preserve cancellation.
#### Scenario: Ownership is lost during fetch
- **WHEN** the claim expires or a successor takes it during pre-consume fetch
- **THEN** the old body cannot admit consume and cannot release the successor
#### Scenario: Renewal awaits past the old deadline
- **WHEN** the claim expires while renewal waits for the write lock or its extension statement
- **THEN** renewal is refused without committing an extended deadline
- **AND** consume admission is refused while a successor remains able to acquire the expired claim
#### Scenario: Cancellation follows acquisition commit
- **WHEN** cancellation arrives after durable commit but before acquire returns
- **THEN** exact-holder cleanup runs and cancellation remains observable
#### Scenario: Renewal uncertainty preserves an unknown outcome
- **WHEN** renewal fails after an attempt has been pinned
- **THEN** the body stops and the durable pin remains for reconciliation
- **AND** cancellation does not assert whether external consume succeeded

### Requirement: Expected reset targets constrain first selection and retries
Dashboard consume SHALL accept `expectedCreditId` and `expectedCreditExpiresAt`. Supplied expectations MUST constrain authoritative first selection and its durable admission pin. A conflicting existing or concurrently won pin MUST conflict before consume. Requests without expectations SHALL retain supported selection/retry behavior.
#### Scenario: Approved credit disappears
- **WHEN** another actor consumes the approved credit before the first request
- **THEN** a later credit cannot be silently selected
#### Scenario: Durable pin contradicts approval
- **WHEN** an existing or concurrently established pin conflicts with expected identity or expiry
- **THEN** consume is refused and the pin remains unchanged
