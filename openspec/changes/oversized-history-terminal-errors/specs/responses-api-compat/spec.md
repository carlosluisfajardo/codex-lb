## ADDED Requirements

### Requirement: Replayed function_call arguments above the upstream string limit fail fast

When a Responses request's `input` contains a `function_call` item whose
`arguments` string is longer than 1,048,576 characters, the service MUST reject
the request locally. The rejection MUST happen before opportunistic admission,
API-key reservation, account selection, bridge session allocation, and any
upstream connection.

- HTTP routes MUST return status `400` with `error.type = "invalid_request_error"`,
  `error.code = "string_above_max_length"`, and
  `error.param = "input[<index>].arguments"`, where `<index>` is the item's
  position in the forwarded `input`.
- WebSocket routes MUST send the same envelope as an error event with
  `"type": "error"` and `"status": 400`.
- The message MUST state the limit and the actual length, and MUST NOT contain
  the arguments.

The service MUST NOT truncate, replace, or annotate the arguments. It MUST NOT
synthesize a different tool call or tool output, and MUST NOT change any other
input item. Arguments at or below the limit MUST be forwarded unchanged.

The limit MUST be measured in code points, so the check never rejects arguments
that upstream would accept. A request whose last `input` item is a
`compaction_trigger` is exempt and keeps the existing compaction flow. Requests
routed to a configured model source are not subject to this check.

#### Scenario: Oversized replayed arguments are refused before upstream

- **GIVEN** an HTTP `/backend-api/codex/responses` or `/v1/responses` request whose history holds a `function_call` with 1,048,577-character `arguments`
- **WHEN** the service receives it, streaming or not
- **THEN** it returns HTTP `400` with code `string_above_max_length` and param `input[<index>].arguments`
- **AND** no admission, reservation, bridge session, or upstream request happens
- **AND** the response body does not contain the arguments

#### Scenario: WebSocket requests receive a status-400 error event

- **GIVEN** a WebSocket `response.create` whose history holds the same `function_call`
- **WHEN** the service prepares the request
- **THEN** the client receives an error event with `"status": 400` and code `string_above_max_length`
- **AND** the service does not connect upstream

#### Scenario: Arguments at the limit are forwarded unchanged

- **GIVEN** a replayed `function_call` whose `arguments` is exactly 1,048,576 characters
- **WHEN** the request is forwarded upstream
- **THEN** upstream receives that item unchanged

#### Scenario: A terminal compaction trigger keeps the compaction flow

- **GIVEN** a request with an oversized replayed `function_call` whose last `input` item is a `compaction_trigger`
- **WHEN** the service receives it
- **THEN** the arguments limit does not reject it

## MODIFIED Requirements

### Requirement: Preserve raw backend stream error frames when contract mode is disabled

The proxy MUST preserve raw backend stream error frames when contract mode is
disabled. When the proxy serves `POST /backend-api/codex/responses` with
`enforce_openai_sdk_contract=False`, it MUST forward upstream HTTP SSE frames
with `type: "error"` unchanged on the stream, except for the request
rejections defined below. In this mode, no `response.failed` synthesis is
allowed before `yield` for those upstream frames.

A request rejection is an upstream error frame that carries status `400` (in
`status` or `status_code`), or that has no numeric status and an error type of
`invalid_request_error`. A `previous_response_not_found` denial is not a
request rejection here; it keeps its own contract. A request rejection MUST
reach the client in a form the client treats as terminal:

- On the HTTP bridge, when the rejection arrives before the downstream response
  commits, the client MUST receive HTTP `400` with the error envelope in the
  body.
- Otherwise the client MUST receive a terminal `response.failed` with the same
  error type, code, and param, followed by `[DONE]`. That terminal MUST NOT be
  marked as a synthetic transport failure.

The delivered message MUST be sanitized: control characters removed,
credential-shaped tokens (bearer values, API keys, JWTs) and email addresses
redacted, and length bounded to 1,000 characters. The request MUST still settle
once:

- one error request-log row
- the API-key reservation released
- no account-health penalty
- no replay or failover for the rejection

An internal bridge owner forward MUST relay the raw frame so that the origin
decides the delivery for its own client.

#### Scenario: Raw backend error passthrough

- **GIVEN** a streaming HTTP upstream response emits:
  `data: {"type":"error","sequence_number":"error","error_type":"server_error",...}`
- **AND** request handling sets `enforce_openai_sdk_contract=False`
- **WHEN** the proxy forwards that upstream event in the public stream
- **THEN** the downstream event MUST remain an `error` event
- **AND** `sequence_number`, `error_type`, and message fields from upstream must remain unchanged
- **AND** the event SHOULD NOT be rewritten into `response.failed` in the same stream step

#### Scenario: Native request rejection before commit

- **GIVEN** a native Codex HTTP request served through the HTTP bridge
- **WHEN** upstream answers with `{"type":"error","status":400,"error":{"type":"invalid_request_error",...}}` before the downstream response commits
- **THEN** the client receives HTTP `400` with that error's type, code, sanitized message, and param
- **AND** the request is settled once without penalizing the account

#### Scenario: Native request rejection after commit

- **GIVEN** a native Codex HTTP request whose downstream response has committed
- **WHEN** upstream answers with the same rejection
- **THEN** the stream ends with one `response.failed` carrying that error, then `[DONE]`
- **AND** no bare `error` frame is delivered
- **AND** the request is settled once without penalizing the account

#### Scenario: Untrusted upstream rejection text is sanitized

- **GIVEN** an upstream rejection whose message holds control characters, a bearer value, an API key, an email address, or more than 1,000 characters
- **WHEN** the rejection is delivered to a native client
- **THEN** the control characters are removed, the tokens and the address are redacted, and the message has at most 1,000 characters
