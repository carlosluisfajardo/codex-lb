## ADDED Requirements

### Requirement: Replayed function_call arguments above the upstream string limit fail fast

When a Responses request's `input` contains a `function_call` item whose
`arguments` string is longer than 1,048,576 characters, the service MUST reject
the request locally. The rejection MUST happen before opportunistic admission,
API-key reservation, account selection, bridge session allocation, and any
upstream connection.

- HTTP routes MUST return status `400` with `error.type = "invalid_request_error"`,
  `error.code = "string_above_max_length"`, and
  `error.param = "input[<index>].arguments"`. `<index>` is the item's position
  in the request's normalized `input`, after leading system and developer
  messages are lifted into `instructions`. It can differ from the client's own
  index and from the position on the wire.
- WebSocket routes MUST send the same envelope as an error event with
  `"type": "error"` and `"status": 400`.
- The message MUST state the limit and the actual length, and MUST NOT contain
  the arguments.

The service MUST NOT truncate, replace, or annotate the arguments. It MUST NOT
synthesize a different tool call or tool output, and MUST NOT change any other
input item. Arguments at or below the limit MUST be forwarded unchanged.

The limit MUST be measured in code points, so the check never rejects arguments
that upstream would accept. The following requests are exempt:

- A request whose last `input` item is a `compaction_trigger` keeps the
  existing compaction flow.
- A request that carries `previous_response_id` is left to upstream, which can
  hold the call server-side while the proxy trims it from the forwarded input.
- A request for a model served by a configured model source is not subject to
  the check. On HTTP the route sends it to the source first. On WebSocket the
  existing connect-time `503 model_source_requires_http_transport` still sends
  the client to HTTP.

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

#### Scenario: An anchored request is left to upstream

- **GIVEN** a request with `previous_response_id` whose `input` holds an oversized `function_call`
- **WHEN** the service receives it
- **THEN** the arguments limit does not reject it

#### Scenario: A source-owned model keeps the WebSocket fallback to HTTP

- **GIVEN** a WebSocket `response.create` for a model served by a configured model source, whose history holds an oversized `function_call`
- **WHEN** the service prepares the request
- **THEN** the client receives the `503 model_source_requires_http_transport` error event, not the arguments refusal

## MODIFIED Requirements

### Requirement: Preserve raw backend stream error frames when contract mode is disabled

The proxy MUST preserve raw backend stream error frames when contract mode is
disabled. When the proxy serves `POST /backend-api/codex/responses` with
`enforce_openai_sdk_contract=False`, it MUST forward upstream HTTP SSE frames
with `type: "error"` unchanged on the stream, except for the request
rejections defined below. In this mode, no `response.failed` synthesis is
allowed before `yield` for those upstream frames.

A request rejection is an upstream `error` event, including a typeless frame
whose `error` is an object. It either carries status `400` (in `status` or
`status_code`), or has no numeric status and an error type of
`invalid_request_error`. A `previous_response_not_found` denial is not a
request rejection here; it keeps its own contract. That exemption MUST be
established from the error code, or from the whole message when the message is
at most 512 characters long. A longer message MUST NOT be exempted on the
strength of its prefix; it is delivered as a request rejection. A request
rejection MUST reach the client in a form the client treats as terminal:

- On the HTTP bridge, when the rejection arrives before the downstream response
  commits, the client MUST receive HTTP `400` with the error envelope in the
  body.
- Otherwise the client MUST receive a terminal `response.failed` with the same
  error type, code, message, and param, followed by `[DONE]`. That terminal MUST
  NOT be marked as a synthetic transport failure.

The delivered error MUST be rebuilt from validated parts and MUST NOT copy
upstream text, because upstream error text and metadata can echo request
bodies, secrets, or account data:

- `type` MUST be `invalid_request_error`.
- `code` MUST be upstream's code only when it is a lowercase snake-case
  identifier of at most 64 characters, and `invalid_request_error` otherwise.
- `param` MUST be upstream's param only when it is a field path of at most 128
  characters, made of identifiers, `[<index>]` and `.`, and MUST be omitted
  otherwise.
- The message MUST restate a recognized diagnostic from its validated values,
  and otherwise MUST be a fixed instruction that names the validated `param`
  and says that retrying the same request fails the same way. Two diagnostics
  are recognized:
  - The argument-length diagnostic `Invalid 'input[<index>].arguments': string
    too long. ...`, whose index is a non-negative integer and whose maximum is
    a positive integer below the actual length. It MUST be delivered with code
    `string_above_max_length`, param `input[<index>].arguments`, the three
    validated numbers, and an instruction to continue in a new conversation
    without the item.
  - The unsupported-model diagnostic, with a validated model slug.
- Recognition MUST read at most the first 512 characters of the upstream
  message. No regular expression on this delivery path may scan more, and text
  outside a recognized diagnostic, before or after it, MUST be discarded.

The rejection that is finally delivered MUST settle once:

- one error request-log row
- the API-key reservation released
- no account-health penalty
- no replay or failover beyond the existing account model-route fallback

An internal bridge owner forward MUST relay the raw frame so that the origin
decides the delivery for its own client. Streams from a configured model source
keep raw passthrough.

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
- **THEN** the client receives HTTP `400` with the error rebuilt from its validated parts
- **AND** the request is settled once without penalizing the account

#### Scenario: Native request rejection after commit

- **GIVEN** a native Codex HTTP request whose downstream response has committed
- **WHEN** upstream answers with the same rejection
- **THEN** the stream ends with one `response.failed` carrying that error, then `[DONE]`
- **AND** no bare `error` frame is delivered
- **AND** the request is settled once without penalizing the account

#### Scenario: Untrusted upstream rejection text and metadata are never copied

- **GIVEN** an upstream rejection with oversized or arbitrary `type`, `code`, or `param`, or whose message is arbitrary text, a body appended to a recognized diagnostic, a diagnostic with invalid numbers or field, a long blank string, or a long token
- **WHEN** the rejection is delivered to a native client before or after commit
- **THEN** the client receives type `invalid_request_error`, the validated or default code, the validated param or none, and either the restated diagnostic or the fixed instruction
- **AND** no upstream text reaches the client

#### Scenario: A truncated prefix never exempts a stale-anchor lookalike

- **GIVEN** an upstream rejection with status `400`, no code or param, and a message that starts with a stale-anchor phrase followed by more text past the 512-character bound
- **WHEN** the rejection is delivered to a native client before or after commit
- **THEN** the client receives the rebuilt rejection with the fixed instruction
- **AND** no raw frame or upstream text reaches the client
- **AND** a whole short stale-anchor message, or a `previous_response_not_found` code, keeps its own contract

#### Scenario: Recognition reads a bounded prefix

- **GIVEN** an upstream rejection whose message is a recognized diagnostic followed by more than a megabyte of text
- **WHEN** the rejection is delivered to a native client
- **THEN** no regular expression on the delivery path scans more than 512 characters of it
- **AND** the client receives the restated diagnostic

### Requirement: Typeless terminal errors retain settlement and correlation data

The streaming normalizers MUST classify a payload with a dictionary `error`
and no string `type` as an `error` event for terminal settlement. A nested
`response.failed` error MUST retain its outer response identifier when its
error details are masked or sanitized. A valid native error frame that needs no
sanitization MUST remain byte-identical, unless it is a request rejection; a
request rejection is delivered as defined in "Preserve raw backend stream error
frames when contract mode is disabled".

#### Scenario: typeless error flushes pending terminal-adjacent state

- **GIVEN** a stream has buffered reasoning-summary data followed by a typeless
  error payload
- **WHEN** the normalizer processes the error
- **THEN** it flushes the buffered data before forwarding the terminal error

#### Scenario: nested terminal masking preserves response id

- **GIVEN** a `response.failed` payload has an outer `response.id` and a stale
  previous-response error
- **WHEN** the public normalizer masks the stale error
- **THEN** the terminal event keeps the same `response.id`
- **AND** the error details are the generic `stream_incomplete` shape

### Requirement: Native HTTP interprets Responses stream events compatibly

Native direct and routed HTTP Responses streams MUST preserve the existing event
classification and legacy text/audio/audio-transcript alias normalization.
Unmodified events MUST retain their original text. SSE field parsing MUST
recognize only CR, LF, and CRLF boundaries and join data fields with LF.
Canonical event-line classification, malformed JSON, native passthrough mode,
unknown fields, and terminal behavior MUST match the Python path.

The synchronous Rust Responses library MUST own eligible alias normalization and
event classification. Python MUST retain request-context-dependent error mapping.
When alias serialization contains floating numbers, integers outside the Rust
JSON integer domain, or escaped surrogate strings, the interpreter MUST signal
Python normalization of the original event rather than lose data or reinterpret
its representation. This handoff MUST NOT replay the request.

#### Scenario: Legacy alias on framing and payload

- **WHEN** an event uses a supported legacy alias in its event line or payload
- **THEN** both surfaces are normalized according to the existing Python rules
- **AND** unrelated data and fields retain their values

#### Scenario: Unsupported alias JSON representation

- **WHEN** rewriting an alias requires a JSON representation outside the native serializer's supported domain
- **THEN** Python receives the original event and an explicit normalization marker
- **AND** the public result matches the ordinary Python transport

#### Scenario: Native error passthrough

- **WHEN** native passthrough receives an error event that is not a request rejection
- **THEN** it remains an error event and ends the stream under the existing policy
- **AND** SDK mode retains the existing request-context-dependent error conversion
- **AND** a request rejection follows "Preserve raw backend stream error frames when contract mode is disabled"

