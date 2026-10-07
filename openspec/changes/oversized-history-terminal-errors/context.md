# Context: oversized history terminal errors

## Purpose

Make a single oversized replayed tool call fail clearly and immediately,
instead of trapping a Codex thread in silent retries.

## Failure mode

- The model emitted a `function_call` whose `arguments` was about 5.1 million
  characters of degenerate text. The client stored it in the thread history.
- Upstream accepts at most 1,048,576 characters in `function_call.arguments`.
  Every later turn replays the history, so every later turn is rejected:
  `Invalid response.create payload: Invalid 'input[66].arguments': string too
  long. Expected a string with maximum length 1048576, but got a string with
  length 5098360 instead.`
- On the native HTTP route the bridge relayed that frame as `event: error` on an
  HTTP `200`, then `[DONE]`.
- The client reported "stream closed before response.completed" and retried
  five times. That is what its HTTP SSE path does at a clean EOF that has no
  terminal it acts on (`response.completed`, `response.failed`,
  `response.incomplete`). The bare `error` event was not acted on. This is
  inferred from the incident and from the repository's earlier client
  analysis, not re-verified against client source here.
- Over the WebSocket transport, the same client maps a wrapped
  `{"type":"error","status":400}` frame to a non-retryable invalid request.
  That is why the HTTP `400` before commit is the right translation for the
  bridge, which carries an HTTP request on an upstream WebSocket.

## Decisions

### Fail closed instead of an omission marker

The proxy refuses the request; it never projects the arguments into something
shorter. An explicit omission marker was considered and rejected:

- `arguments` is model-authored tool input, not tool output. Replacing it puts
  a tool request the model never made into its own history; the model may act
  on the marker, for example by re-issuing the call with invented arguments.
- Side-effect replay dedupe keys on tool name, call id and canonical arguments
  (`request_user_input` is a side-effect tool). Rewritten arguments change that
  identity.
- The existing historical-slimming contract covers tool outputs and inline
  images only. Outputs are data; arguments are instructions.
- Whether upstream accepts a marker in place of schema-conforming arguments for
  strict tools cannot be established without provider traffic.
- A blanket string slice yields invalid JSON and a truncated, still-executable
  instruction.

The refusal keeps call and output identities untouched, never alters persisted
history (codex-lb never persists the client's history), and makes the cause
visible.

### Where the guard runs

- HTTP: `_stream_responses` and `_collect_responses`, after the compaction
  branch and before opportunistic admission and the API-key reservation.
  Nothing is reserved yet, so nothing needs settling. Like other request
  validation `400`s, the refusal writes no request-log row.
- WebSocket: `_prepare_websocket_response_create_request`, after the
  source-route exclusion is known and before the reservation. It raises
  `ClientPayloadError`, which the existing handler sends as a status-400 error
  event.
- Model sources are unaffected, because they may not share the upstream limit.
  - On HTTP the route sends a source-owned request to the source before these
    points.
  - On WebSocket, when the guard finds an oversized argument it first checks
    source ownership. A source-owned model (not route-excluded) is left to the
    connect-time `503 model_source_requires_http_transport`, which sends the
    Codex client to HTTP.
- A request that carries `previous_response_id` is left to upstream. Upstream
  can hold the call server-side, and the proxy trims previous-response output
  items from such an input before sending it.
- A full-history request (no `previous_response_id`) is judged as the client
  sent it, matching the existing `response.create` size guard: a full resend
  must stay servable for replay and failover. A bridge- or session-anchored
  turn, whose anchor the proxy injects itself, would have trimmed the item, so
  it now fails one turn earlier. That thread fails on its next reconnect anyway.
- A terminal `compaction_trigger` is exempt, because compaction is the path
  that can still summarize such a history. Upstream decides whether its compact
  endpoint accepts the item.

### Which error frames are request rejections

- A request rejection is an `error` event with `status`/`status_code` `400`, or
  one with no numeric status whose error type is `invalid_request_error`. Event
  classification is the shared one, so a typeless frame with an `error` object
  counts.
- Rate limits, server errors and status-less frames of other types keep raw
  passthrough.
- A `previous_response_not_found` denial keeps its masking contract.
- An internal owner forward relays the raw frame; the origin converts it for
  its own client.
- The pre-commit conversion runs when the route admitted the HTTP bridge,
  which is the same gate the SDK conversion uses. That includes requests the
  service later bypasses to the direct path for size or images. On a route
  without the bridge the conversion happens in-stream.
- Closing the stream after the converted frame settles exactly once:
  - On the bridge, the terminal request state is popped from the pending set
    before the frame is published, so the detach takes its non-pending branch
    and neither releases nor retires the session. Request-log and reservation
    settlement then run on the reader.
  - The service signals cleanup-ready after submit, so the route does not
    release the reservation a second time.
  - The direct path settles in its `GeneratorExit` handler when the probe
    closes it.

### Untrusted upstream text

Upstream error text and metadata are never copied to the client.
`public_request_rejection` (`app/core/errors.py`) rebuilds the error from
validated parts only. A well-formed value is not proof it is public: an
earlier revision kept any snake-case code, any identifier path and any model
slug, so a dotted JWT passed as `param` and an `sk-` key shape in the model
diagnostic reached the client. Delivered text now comes only from fixed
vocabularies and re-rendered integers:

- `type` is always `invalid_request_error`.
- `code` is kept only when it is one of the public Responses request
  rejection codes listed in `_PUBLIC_REQUEST_ERROR_CODES`, and is otherwise
  `invalid_request_error`. The list includes the codes the Codex client
  classifies on their own (`context_length_exceeded`, `invalid_prompt`,
  `cyber_policy`, `bio_policy`, `misalignment_policy_violation`): on
  `response.failed` it treats an unknown code as retryable, so rewriting one of
  them would lose the client's classification.
- `param` is kept only when it is at most 128 characters and 8 segments, and
  every segment is a Responses request field name listed in
  `_PUBLIC_REQUEST_FIELDS`, optionally indexed once with `[<index>]`; it is
  otherwise dropped.

This bounds what the proxy itself copies. It is not a claim that an
unrecognized value is free of secrets: the vocabularies are what make the
delivered text known.

The message is built in one of three ways:

- The argument-length diagnostic is restated from its validated index, maximum
  and actual length. The index must be non-negative, and the maximum must be
  positive and below the actual length. It is delivered as
  `string_above_max_length` with param `input[<index>].arguments`.
- The unsupported-model diagnostic, which the model-fallback replay surfaces,
  becomes fixed text: "The requested model is not supported when using Codex
  with a ChatGPT account. Retrying the same request fails the same way; choose
  a different model." Upstream's slug is not copied.
- Anything else, including an appended body, invalid numbers, a blank string or
  a long token, gets a fixed instruction naming the validated param:
  "Upstream rejected the request as invalid at '<param>'. Retrying the same
  request fails the same way; change the request or continue in a new
  conversation."

Bounds on this path:

- Recognition reads at most the first 512 characters of the raw message, and
  no regex on this path scans more.
- The stale-anchor exemption is decided from the error code, or from the whole
  message when it fits in those 512 characters. A longer message is never
  exempted because of its prefix: an earlier revision did that, so a
  stale-anchor phrase followed by an arbitrary body passed through raw. Such a
  message is now delivered as a rebuilt rejection.
- Nothing from the raw text is copied, so a cut token, a blank string or a
  missing whitespace break can only fail recognition and fall back to the fixed
  instruction.

An earlier revision pattern-redacted the upstream message. That was replaced
because redaction cannot establish that an arbitrary body or secret is gone,
and because it normalized the full raw message before bounding it. Request logs
keep the upstream text for operators.

## Example

A native client replays a history whose item 66 is a `function_call` with
5,098,360 characters of arguments. The proxy answers HTTP `400`:

```json
{"error": {"type": "invalid_request_error", "code": "string_above_max_length",
  "param": "input[66].arguments",
  "message": "Invalid 'input[66].arguments': string too long. Expected a string with maximum length 1048576, but got a string with length 5098360 instead. Upstream rejects this replayed function_call (call_id call_…) on every request and codex-lb does not truncate or rewrite tool-call arguments; continue in a new conversation without this item."}}
```

The client stops and shows the message instead of retrying.

## Operations

- A thread that hits this cannot continue as-is; no retry, model switch or
  proxy restart changes its history. Continue in a fresh conversation, seeded
  with a bounded handoff of the work in progress. Do not fork from before the
  bad item: that keeps the same near-full context. Keep the original thread for
  reference instead of editing its stored history.
- Long-lived threads approaching the context window, with very large stored
  history or multi-megabyte compaction summaries, are worth rotating to a fresh
  conversation early. Those signals were observed with this failure; they are
  not a proven cause or a universal threshold.

## Limitations

- Only `function_call.arguments` is checked. The upstream limits for
  `custom_tool_call.input` and tool outputs are not established here.
- `param` indexes the request's normalized `input`. The proxy lifts leading
  system/developer messages into `instructions` and strips some items on the
  wire, so the index can differ from the client's own and from the wire
  position. The call id in the message identifies the item.
- After commit, a client that does not recognize the error code in
  `response.failed` may still retry; each attempt fails fast with the real
  message.
- Upstream error text on the SDK routes and on WebSocket passthrough keeps its
  existing handling.
- Streams from a configured model source keep raw passthrough, including their
  `error` frames.
