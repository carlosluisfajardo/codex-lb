# Keep new tool call/result pairs on WebSocket continuations

## Why

A direct WebSocket `response.create` that carries `previous_response_id` may
start its `input` with items the client replayed from the previous response
(reasoning, an assistant message, the tool call) before the first tool output.
The service removes that replayed prefix, because the upstream response
already holds those items.

It decides what is replayed by item type alone. Every leading `function_call`,
`custom_tool_call` and `apply_patch_call` before the first tool output is
removed, even when the call is new: a client that adds a complete call and its
output at the start of a continuation loses the call, and the upstream receives
an output whose call it never saw. An existing unit test pinned exactly that
removal for a `custom_tool_call` whose own output follows it.

The service already records, for every completed response, the `call_id` and
type of each tool-call `response.output_item.done` event routed to it, next to
that response id, on the session's continuity state. Output frames usually carry
no response id, though. On a pipelined socket with several created responses,
the relay's fallback can route a frame to a response that did not emit it. Only
a call whose ownership is certain proves that a leading call is replay: its
event named the response id, or arrived while that response was the only
created one.

## What Changes

- Remove a leading tool call only when its `call_id` and type match a tool call
  proven for the response named by `previous_response_id` on the same
  continuity state.
- Record proven tool calls in their own map on the request and continuity
  state. The existing pending record keeps driving the interrupted-output
  injection and replay-safety checks unchanged.
- Keep every other leading tool call in place and in order, so a new
  call/result pair reaches the upstream intact. A request that refers to a
  response the service has no record of keeps all of its tool calls.
- Leave assistant messages and reasoning items in that prefix, the
  non-replay-prefix guard, previous-response ownership, continuity anchors and
  interrupted-output injection unchanged.
- Replace the test expectations that blessed removal by type, and add
  regression coverage for new pairs of all three call types, recorded replay,
  unknown and mismatched identity, call id versus item id, ordering and
  ordinary continuations.

## Non Goals

- No schema, setting or API change. The only new state is one in-memory map of
  proven tool calls on the request and continuity state.
- No synthesized, duplicated or resent tool calls, tool outputs or side effects.
- No change to the HTTP bridge counterpart, which classifies replay by a
  response-output marker (`id` or `status`) rather than by type alone.
- No change to how assistant messages and reasoning items are recognized: they
  carry no recorded identity and cannot break call/result pairing.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: adds the requirement that WebSocket continuations
  remove only tool calls proven to be previous-response replay.

## Impact

- Code: `app/modules/proxy/_service/websocket/helpers.py` (trim helper,
  ownership predicate, continuity record), `app/modules/proxy/_service/websocket/mixin.py`
  (tool-call collector and the trim call site) and
  `app/modules/proxy/_service/support.py` (the proven-call map fields).
- Tests: `tests/unit/test_proxy_utils.py` and
  `tests/integration/test_proxy_websocket_responses.py`.
- Behavior: a continuation that references a response the service did not
  observe on that continuity state now forwards its leading tool calls instead
  of dropping them. Assistant message and reasoning items keep their existing
  treatment.
- Compatibility and rollback: see `context.md`.
