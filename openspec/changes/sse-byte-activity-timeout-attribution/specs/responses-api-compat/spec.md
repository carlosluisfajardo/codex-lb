## ADDED Requirements

### Requirement: HTTP SSE timeout attribution follows received body bytes

Python subscription HTTP Responses MUST use received nonempty upstream body
chunks as transport activity when attributing a generic timeout. If the original
overall request deadline expires while incomplete SSE body chunks continue
arriving within the upstream idle allowance, the terminal MUST retain
`upstream_request_timeout`, request identity and nonretryable metadata. Activity
MUST NOT renew the original overall deadline. Genuine upstream receive-idle
expiry, including its existing tie preference after headers, MUST retain
`stream_idle_timeout`. Local downstream keepalives MUST NOT count as upstream
activity. Cancellation, transport-error classification, cleanup, account
ownership, reservation settlement and replay restrictions MUST remain unchanged.

#### Scenario: Total expires during an incomplete active event

- **GIVEN** a 600-second upstream idle allowance and an original 7200-second total budget
- **WHEN** incomplete SSE bytes arrive every 300 seconds until the total deadline
- **THEN** the response terminates as `upstream_request_timeout` at the original deadline
- **AND** it preserves request identity and releases the attempt without replay

#### Scenario: Actual silence still expires at idle

- **GIVEN** upstream headers and an incomplete SSE body fragment have arrived
- **WHEN** no upstream bytes arrive for the 600-second idle allowance
- **THEN** the response terminates as `stream_idle_timeout`
- **AND** local downstream keepalives cannot extend that allowance

#### Scenario: Complete events do not extend total

- **GIVEN** complete SSE events arrive within each idle allowance
- **WHEN** the original total deadline expires
- **THEN** the response terminates as `upstream_request_timeout` without extending its total budget
