## ADDED Requirements

### Requirement: Dashboard authentication cookies remain local
All outbound proxy HTTP and WebSocket transports, including control and native transports, MUST exclude `codex_lb_dashboard_session` and `codex_lb_step_up`. Required upstream authorization, account identity and ordinary permitted headers MUST remain intact. Transports already dropping all cookies SHALL retain that policy.
#### Scenario: Prepared transport excludes local credentials
- **WHEN** a request mixes dashboard credentials with ordinary permitted headers or cookies
- **THEN** no local authentication cookie reaches the prepared transport
- **AND** required ordinary forwarding and upstream authentication remain intact

### Requirement: Keyless browser WebSockets require same-origin admission
A keyless WebSocket MUST reject foreign, opaque, malformed or ambiguous browser origins before acceptance or dispatch on every supported responses route. Origin scheme, hostname and effective port MUST match the request; cross-site fetch metadata MUST fail. Nonbrowser clients without browser metadata and valid API-key clients SHALL retain supported access.
#### Scenario: Foreign origin cannot use loopback keyless trust
- **WHEN** a local keyless request carries a foreign Origin, even with claimed same-origin fetch metadata
- **THEN** HTTP 403 is returned before socket acceptance or dispatch
#### Scenario: Port zero is distinct from the default port
- **WHEN** Origin uses port zero and the request uses the default HTTP port
- **THEN** admission fails
#### Scenario: Supported CLI and authenticated clients
- **WHEN** an authorized CLI omits browser metadata or a client supplies a valid API key
- **THEN** the browser-origin predicate does not deny the request

### Requirement: Retained direct WebSockets enforce routing blocks before dispatch
Every direct WebSocket `response.create` MUST check the serving account's current routing-unavailable cache at the final send boundary, including ordinary frames without capability or conversation requirements. A pause or routing hold completed before dispatch, including during admission or account-capacity waits, MUST prevent fresh work from being sent on the retained socket. Refusal MUST settle that unsent frame's admission resources without stripping ownership anchors, selecting another account or cancelling unrelated in-flight work. The check MUST NOT add a per-frame database read. Work already dispatched has a separate outcome and is not cancelled or proven unspent by this check.
#### Scenario: Ordinary frame follows a successful routing block
- **WHEN** request one completes on a retained direct socket and its account is successfully paused or held before ordinary request two
- **THEN** request two receives a terminal refusal and is not sent upstream
- **AND** the retained socket does not opportunistically rebind to a different account
#### Scenario: Routing block completes during account admission
- **WHEN** request two waits for account admission and a pause or hold completes before the wait ends
- **THEN** the final send check refuses the frame before upstream dispatch
#### Scenario: Explicit release permits later work
- **WHEN** the account is explicitly reactivated or its hold released after a refused frame
- **THEN** an eligible later frame can use the same retained socket and its released admission resources
