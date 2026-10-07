# Fail fast on oversized replayed tool-call arguments and deliver request rejections to native Codex clients

## Why

A Codex thread's history held one `function_call` whose `arguments` string was
about 5.1 million characters. Upstream caps that field at 1,048,576 characters,
and the client replays the whole history every turn, so every later request was
rejected upstream with `invalid_request_error`
(`Invalid 'input[N].arguments': string too long`).

codex-lb recorded each rejection correctly: an error request-log row, the
reservation released, the account left healthy. On
`/backend-api/codex/responses`, though, it relayed the upstream frame to the
native client as a bare `event: error` block on an HTTP `200`, then `[DONE]` and
a clean EOF. The Codex client does not treat a bare `error` SSE frame as a
terminal event. It read the EOF as a dropped stream ("stream closed before
response.completed"), resent the same rejected history five times, and never
showed the real reason. Retrying, switching models, or restarting the proxy
could not help while the item stayed in the history.

## What Changes

- Requests whose `input` holds a `function_call` with `arguments` longer than
  1,048,576 characters are rejected locally before admission, reservation,
  account selection or any upstream work. HTTP routes get a `400`
  (`invalid_request_error`, code `string_above_max_length`, param
  `input[N].arguments`); WebSocket routes get a status-400 error event. The
  message names the limit and the call, says to continue in a new conversation,
  and never echoes the arguments.
- codex-lb does not truncate, replace or mark the arguments.
- Some requests are left to upstream or their source:
  - a terminal compaction trigger keeps the upstream compact flow;
  - a request anchored with `previous_response_id` is left to upstream;
  - models served by a configured model source are unaffected.
- On the native Codex HTTP route, an upstream request rejection is delivered in
  a form the client reads. A rejection is a wrapped error frame with status
  `400`, or a frame with no numeric status typed `invalid_request_error`.
  - Before the response commits on the HTTP bridge, it is the HTTP `400` an
    HTTP upstream returns.
  - Otherwise it is a terminal `response.failed` followed by `[DONE]`.
  - Other error frames keep raw passthrough.
  - The delivered error never copies upstream text or metadata:
    - `type` is fixed;
    - `code` and `param` are kept only from listed request rejection codes and
      Responses request field names, or replaced or dropped;
    - the message restates the argument-length diagnostic from re-rendered
      numbers, states an unsupported model as fixed text, or is a fixed
      instruction;
    - recognition reads at most 512 characters of the raw message.
- Settlement is unchanged and is now pinned by tests for the rejection that is
  finally delivered: one error request-log row, the API-key reservation
  released, no account-health penalty, and no replay or failover beyond the
  existing account model-route fallback.

## Capabilities

### Modified Capabilities

- responses-api-compat

## Impact

- Code: `app/modules/proxy/request_policy.py`, `app/modules/proxy/api.py`,
  `app/modules/proxy/_service/websocket/mixin.py`, `app/core/errors.py`.
- Native Codex clients now see a real error for an upstream request rejection
  where they previously saw a truncated stream. A pre-commit `400` is
  non-retryable for them. A post-commit `response.failed` with an unrecognized
  code may still be retried by the client, but each attempt now carries the
  real message.
- No new configuration, schema, migration or persistence change.
