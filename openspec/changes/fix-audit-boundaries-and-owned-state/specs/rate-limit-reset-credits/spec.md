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

### Requirement: Dashboard redemption revalidates account eligibility after waits
The shared dashboard and automatic redemption helper MUST re-read the authoritative account after acquiring redemption serialization and again after pre-consume credential, route, fetch, pin and claim-renewal waits. Before dispatch, the account MUST still exist, lack a pending-deletion marker, retain the intended local and upstream account identity, and satisfy the existing reset-redemption eligibility predicate. A routing hold alone MUST NOT make an otherwise eligible account ineligible. Refusal MUST preserve committed account state, settle the owned claim and retain any existing request pin for reconciliation. The final database observation does not make the subsequent external consume atomic with later account mutations.
#### Scenario: Account becomes ineligible while waiting for serialization
- **WHEN** a pause or other non-redeemable status commits while redemption waits for its predecessor
- **THEN** the waiter refuses before credential or upstream work and does not consume a credit
#### Scenario: Account changes during pre-consume work
- **WHEN** an account becomes non-redeemable, is deleted or changes identity during credential, route, fetch or admission-renewal waits
- **THEN** the final account check refuses fresh consume without overwriting that change
#### Scenario: Independently held account remains eligible for redemption
- **WHEN** an otherwise eligible account has an independent routing hold
- **THEN** redemption retains the supported exact-credit, request-pin and claim-ownership behavior
#### Scenario: Claim ownership changes during final account observation
- **WHEN** the SQLite claim expires or changes holder while the final account read awaits
- **THEN** account eligibility and the exact claim holder/deadline are observed in one SELECT
- **AND** the observed deadline is checked after that await, without another await before consume admission
- **AND** lost ownership refuses consume, retains the request pin and cannot release a successor claim
