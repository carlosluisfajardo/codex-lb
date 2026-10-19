## ADDED Requirements

### Requirement: Subscription silence is bounded independently of model token latency
Subscription HTTP and WebSocket Responses MUST default to a 600-second upstream transport idle allowance and retain the 7200-second overall stream budget. Existing explicit idle and overall budget overrides MUST remain effective. The proxy MUST NOT impose a first-model-token deadline. Received upstream HTTP body bytes or WebSocket application frames, including response lifecycle events, MUST renew the transport-read idle allowance without extending the original overall budget. WebSocket protocol ping/pong liveness and the bridge response-start watchdogs MUST retain their separate contracts. Locally emitted keepalives MUST NOT renew upstream idle deadlines.

Existing HTTP bridge response-start watchdogs and retry proof guards MUST remain effective. A timeout MUST NOT authorize replay after partial output, pending tool effects, exposed sequence state or ambiguous account-bound work, nor relax file, turn or response-owner pins. Settlement MUST precede any applicable health write; shared-link silence alone MUST NOT penalize an account.

#### Scenario: Local WebSocket keepalives cannot hide a stall
- **GIVEN** a subscription WebSocket request with a 600-second idle allowance and a 7200-second total budget
- **WHEN** the upstream remains silent while the proxy emits downstream keepalives
- **THEN** the request terminates at the idle deadline with the existing timeout envelope
- **AND** the upstream socket, pending request and reservations are released without replay

#### Scenario: Long thinking with upstream activity remains valid
- **GIVEN** a response with no model output token yet
- **WHEN** upstream lifecycle events arrive within each idle allowance
- **THEN** the response remains active beyond 600 seconds
- **AND** the original overall request deadline remains in force

## MODIFIED Requirements

### Requirement: HTTP SSE stream idle timeouts remain account-neutral

When an HTTP SSE Responses stream's first upstream event is `response.failed` with `code=stream_idle_timeout`, the proxy MUST terminate that attempt without replaying the request or selecting another account. Silence does not prove that upstream work was not accepted. It MUST NOT write account error-health (`record_error`, rate-limit, quota, or permanent failure) for that idle timeout. Request logs MUST still record `stream_idle_timeout` on the idle attempt. Independently proven pre-dispatch connection failures retain their existing bounded retry guards.

#### Scenario: First-event stream idle timeout terminates without replay or health penalty

- **GIVEN** an HTTP SSE Responses stream whose first upstream event is `response.failed` with `code=stream_idle_timeout`
- **AND** another healthy account is available
- **WHEN** the proxy settles the timeout
- **THEN** no second account selection or upstream dispatch occurs
- **AND** the idle account receives no error-health write
- **AND** the client receives the timeout failure
- **AND** the attempt's request log uses `error_code=stream_idle_timeout`
