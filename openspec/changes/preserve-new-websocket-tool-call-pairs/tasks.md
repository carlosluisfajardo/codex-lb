## 1. Specification

- [x] 1.1 Add the `responses-api-compat` requirement that WebSocket continuations
  remove only tool calls proven to be previous-response replay.
- [x] 1.2 Record the identity source, decisions, failure modes, an example and
  rollback in `context.md`.

## 2. Behavior

- [x] 2.1 Classify a leading tool call as replay only when its `call_id` and type
  match a call recorded for the referenced previous response; keep every other
  call in place and in order.
- [x] 2.2 Supply the recorded calls from the continuity state only when
  `previous_response_id` equals its `last_completed_response_id`.
- [x] 2.3 Admit a done tool call to the proven record only when its event named
  the response id or arrived while that response was the only created one.

## 3. Regression coverage

- [x] 3.1 Unit and WebSocket tests for a new call/result pair of each call type,
  seen failing before the fix.
- [x] 3.2 Controls: recorded replay is removed, unknown previous response,
  other recorded call, mismatched type, call id versus item id, mixed order and
  ordinary continuations.
- [x] 3.3 Replace the expectations that blessed removal by type.
- [x] 3.4 Pipelined-socket discriminator (A and B created, C not created, an
  anonymous done event of A's call routed to C), seen failing before the
  correction, plus explicit-response-id and ownership-predicate controls.

## 4. Validation

- [x] 4.1 Ruff check and format, architecture checks and `ty check`.
- [x] 4.2 The WebSocket-relevant unit and integration suites.
- [x] 4.3 `openspec validate preserve-new-websocket-tool-call-pairs --strict`
  and `openspec validate --specs`.
