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
Dashboard consume SHALL accept `expectedCreditId` and `expectedCreditExpiresAt`. Supplied expectations MUST constrain authoritative first selection and its durable admission pin. A conflicting existing or concurrently won pin MUST conflict before consume. A request carrying either expectation MUST reach the authoritative pre-consume fetch even when the local snapshot is stale, empty or absent; only that fetch, a durable pin or the eligibility and claim checks may refuse it. Requests without expectations SHALL retain supported selection/retry behavior, including the cached-availability refusal.
#### Scenario: Approved credit disappears
- **WHEN** another actor consumes the approved credit before the first request
- **THEN** a later credit cannot be silently selected
#### Scenario: Durable pin contradicts approval
- **WHEN** an existing or concurrently established pin conflicts with expected identity or expiry
- **THEN** consume is refused and the pin remains unchanged
#### Scenario: Stale or absent cache cannot refuse an approved target
- **WHEN** the local snapshot is empty or absent but upstream lists the expected credit
- **THEN** the request fetches upstream and consumes exactly that credit
- **AND** a fetch lacking the expected credit or expiry still refuses without consume or pin

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

### Requirement: Explicit fresh reset-credit reads prove their upstream observation
`POST /api/accounts/{id}/rate-limit-reset-credits/refresh` SHALL require dashboard account write access and SHALL read one account's reset credits from upstream, including paused, routing-held and reauthentication-required accounts whose credentials remain usable. A success MUST come only from that request's fetch and MUST return the existing snapshot fields with `accountId` and `fetchedAt`. A fetch, credential or route failure MUST be an error response, never an empty or cached snapshot. The read MUST NOT consume, pin, change policy or hold, or replace the cached snapshot, and MUST NOT change status except through the existing credential-refresh failure handling. Deactivated or identity-less accounts MUST be refused before upstream work. The account MUST be re-read after the fetch: deletion MUST return not found and a changed upstream account identity MUST conflict instead of returning the observation.
#### Scenario: Fresh empty differs from failure and stale cache
- **WHEN** the cache still lists a credit and upstream returns no credits or fails
- **THEN** the response reports the fresh empty result with its fetch time, or an error for the failure
- **AND** the cached snapshot is unchanged
#### Scenario: Held account is inspected while serving stays blocked
- **WHEN** a paused or reauthentication-required account with a routing hold is read
- **THEN** routing excludes it throughout the fetch and its status, policy and hold are unchanged
#### Scenario: Identity changes during the fetch
- **WHEN** the account is deleted or re-imported into another upstream identity before the fetch returns
- **THEN** the observation is refused instead of being returned as fresh

## MODIFIED Requirements

### Requirement: Operators can redeem the soonest-expiring available credit

The system SHALL expose a dashboard endpoint `POST /api/accounts/{account_id}/rate-limit-reset-credits/consume` that redeems exactly one credit for the named account. The endpoint SHALL select, from the freshest cached snapshot, the credit whose `status` is `available` with the smallest `expires_at`, generate a `redeem_request_id` (UUID v4), and forward `{credit_id, redeem_request_id}` to upstream `POST /wham/rate-limit-reset-credits/consume` using the account's bearer token and `chatgpt-account-id`. Before forwarding the consume, the endpoint SHALL durably record the selected `credit_id` against the request's `redeem_request_id` in the shared database; a retry carrying the same `redeem_request_id` MUST reuse that recorded `credit_id` even when served by a different replica. A cached snapshot with `available_count <= 0` MUST be treated as having no redeemable credits, even if the cached `credits` list contains an item marked `available`. When the fresh pre-consume fetch reports `available_count <= 0` or no available credit items, the endpoint SHALL replace any prior cached snapshot for that account with the fresh upstream snapshot before returning a conflict. This SHALL hold even when the caller supplies a `redeem_request_id` for which no durable ledger pin exists: absent a durable pin there is no proof the request is an idempotent retry, so the fresh empty fetch is authoritative and the endpoint MUST NOT pin and consume a stale cached credit. Only a pre-existing durable pin (`(account_id, redeem_request_id) -> credit_id`) authorizes forwarding that pinned credit to upstream when the fresh fetch shows no currently-available credit. On a 200 response the endpoint SHALL invalidate the cached snapshot for that account and return `{code, windows_reset, redeemed_at}`. The endpoint SHALL require dashboard write access; read-only guests MUST be refused.

#### Scenario: Consume selects the soonest-expiring credit
- **GIVEN** an account has cached credits with expiries `2026-07-10Z` and `2026-06-20Z`, both `status: available`
- **WHEN** the operator invokes `POST /api/accounts/{id}/rate-limit-reset-credits/consume`
- **THEN** the request forwarded to upstream carries the `credit_id` whose `expires_at` is `2026-06-20Z`

#### Scenario: Successful consume invalidates the cache
- **GIVEN** the operator invokes consume for an account with at least one available credit
- **WHEN** upstream returns `200` with `{code: "reset", windows_reset: 1, credit: {...}}`
- **THEN** the cached snapshot for that account is invalidated
- **AND** the response returned to the dashboard is `{code, windows_reset, redeemed_at}` derived from the upstream response

#### Scenario: Concurrent consume requests for one account are serialized
- **GIVEN** two operators invoke `POST /api/accounts/{id}/rate-limit-reset-credits/consume` at nearly the same time for the same account, whether both requests reach one process or different processes/replicas sharing the database (on both PostgreSQL and SQLite)
- **WHEN** the first request is still redeeming a credit
- **THEN** the second request MUST wait for the first request to finish before re-reading that account's cached snapshot
- **AND** the same cached `credit_id` MUST NOT be sent to upstream twice by those concurrent requests

#### Scenario: Same-redeem-request retry on another replica reuses the recorded credit
- **GIVEN** a consume with `redeem_request_id` R recorded `credit_id` C durably and forwarded the consume, but the client response was lost
- **WHEN** the retry with the same R is served by a different replica
- **THEN** that replica forwards C to upstream instead of selecting a new credit

#### Scenario: No-body consume synthesizes a redeem_request_id and pins the ledger
- **GIVEN** a dashboard consume request that carries no `redeem_request_id` (the still-supported no-body path)
- **WHEN** the endpoint selects an available credit to redeem
- **THEN** the endpoint synthesizes a UUID v4 `redeem_request_id`, durably pins the selected `credit_id` to it before the upstream consume, and forwards that recorded id to upstream
- **AND** the consume never forwards an unrecorded `redeem_request_id`

#### Scenario: Upstream consume failures surface as dashboard errors
- **GIVEN** an operator invokes `POST /api/accounts/{id}/rate-limit-reset-credits/consume`
- **WHEN** upstream returns `401`, `403`, or `409`
- **THEN** the dashboard endpoint returns the same client-facing status class instead of a generic `500`
- **AND** other upstream consume failures return a dashboard `503`

#### Scenario: Read-only guests cannot redeem
- **GIVEN** a dashboard session authenticated as a read-only guest
- **WHEN** the guest invokes `POST /api/accounts/{id}/rate-limit-reset-credits/consume`
- **THEN** the request is refused before any upstream call is made

#### Scenario: Consume with no available credit returns a client error
- **GIVEN** an account whose cached snapshot reports `available_count: 0` (or has no snapshot)
- **AND** the request carries neither an expected credit identity or expiry nor a durably pinned `redeem_request_id`
- **WHEN** the operator invokes `POST /api/accounts/{id}/rate-limit-reset-credits/consume`
- **THEN** the endpoint returns a `409` (or equivalent client-error) without calling upstream

#### Scenario: Fresh empty consume fetch replaces a stale cached snapshot
- **GIVEN** an account has a cached reset-credits snapshot showing at least one available credit
- **AND** the fresh pre-consume upstream fetch returns `available_count: 0` or no `status: available` items
- **WHEN** the operator invokes `POST /api/accounts/{id}/rate-limit-reset-credits/consume`
- **THEN** the endpoint returns a `409` (or equivalent client-error)
- **AND** the cached snapshot for that account is replaced with the fresh upstream snapshot before the response is returned

#### Scenario: Retry-shaped request without a durable pin returns conflict on an empty fetch
- **GIVEN** an account has a stale cached snapshot showing at least one available credit
- **AND** the caller supplies a `redeem_request_id` for which no durable ledger pin exists
- **AND** the fresh pre-consume upstream fetch returns `available_count: 0` or no `status: available` items
- **WHEN** the operator invokes `POST /api/accounts/{id}/rate-limit-reset-credits/consume`
- **THEN** the endpoint returns a `409` without calling upstream consume
- **AND** no ledger pin is written for that `redeem_request_id`
- **AND** the cached snapshot is replaced with the fresh (empty) upstream snapshot
