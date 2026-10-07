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
- WebSocket: `_prepare_websocket_response_create_request`, right after payload
  normalization and before the reservation. It raises `ClientPayloadError`,
  which the existing handler sends as a status-400 error event.
- Model-source routing decides before these points, so source-routed requests
  (which may not share the upstream limit) are unaffected.
- The guard judges the full forwarded `input`, matching the existing
  `response.create` size guard. A full resend must stay servable for replay and
  failover. A bridge-anchored turn that would have trimmed the item therefore
  now fails one turn earlier; that thread fails on its next reconnect anyway.
- A terminal `compaction_trigger` is exempt, because compaction is the path
  that can still summarize such a history. Upstream decides whether its compact
  endpoint accepts the item.

### Which error frames are request rejections

- A request rejection is a frame with `status`/`status_code` `400`, or a frame
  with no numeric status whose error type is `invalid_request_error`.
- Rate limits, server errors and status-less frames of other types keep raw
  passthrough.
- A `previous_response_not_found` denial keeps its masking contract.
- An internal owner forward relays the raw frame; the origin converts it for
  its own client.
- The pre-commit conversion is limited to the HTTP bridge. There the request is
  finalized before the frame is yielded, which is the same condition the SDK
  conversion already relies on. On the bridge-off path the conversion happens
  in-stream.

### Untrusted upstream text

- Before delivery in this path, the message has control characters turned into
  spaces.
- Bearer values, `sk-`/`rk-`/`pk-`/`sess-` keys, JWT-shaped tokens and email
  addresses are redacted.
- The text is bounded to 1,000 characters.
- Request logs keep the upstream text for operators.

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
- `param` indexes the forwarded `input`. The proxy can lift leading
  system/developer messages into `instructions`, so the index can differ from
  the client's own; the call id in the message identifies the item.
- After commit, a client that does not recognize the error code in
  `response.failed` may still retry; each attempt fails fast with the real
  message.
- Upstream error text on the SDK routes and on WebSocket passthrough keeps its
  existing handling.
