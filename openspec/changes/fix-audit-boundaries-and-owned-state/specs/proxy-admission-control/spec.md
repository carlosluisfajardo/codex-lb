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
