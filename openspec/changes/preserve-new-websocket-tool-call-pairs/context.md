# Context

Normative requirements live in `specs/responses-api-compat/spec.md`. This file
explains where the identity comes from, why the change stops where it does, and
how to back it out.

## Purpose and scope

The WebSocket continuation trim exists for clients that replay the previous
response's output in front of the tool outputs they send with
`previous_response_id`. It must keep doing that for real replays and must stop
removing tool calls that are new. The change is confined to the classification
of leading tool calls in `_trim_websocket_previous_response_input_items` and to
the one call site that supplies the identity.

## Where the identity comes from

For every completed response the WebSocket path records, on the session's
`_WebSocketContinuityState`:

- `last_completed_response_id`: the response that just completed;
- `last_pending_tool_call_types`: `call_id` to item type for every
  `function_call`, `custom_tool_call` and `apply_patch_call` that response
  emitted as a `response.output_item.done` event.

The state is keyed by the session's continuity aliases and API key, or held for
the life of one connection when session affinity is off. The same record already
drives the interrupted-tool-output injection for that response.

A leading tool call is replay exactly when the request's `previous_response_id`
equals `last_completed_response_id` and the call's `call_id` maps to its own
item type in that record. The `call_id` pairs a call with its output; the item
`id` (`fc_...`) is a separate identifier and is never used as call identity.

## Decisions and the alternatives rejected

- **Identity, not shape.** A new pair and a replayed call both arrive with their
  output in the same request, so pair completeness cannot tell them apart. Only
  the recorded emission can.
- **Unknown stays in.** A request that refers to a response the service did not
  observe on this continuity state (another process, an evicted session, a
  connection without session affinity that never saw it) keeps its tool calls.
  Removing them could orphan an output; keeping a genuinely replayed call at
  worst repeats context the upstream already holds.
- **Remove exactly what is proven.** Recorded calls are removed, unproven calls
  stay where they were. Earlier the whole prefix went or stayed together.
- **Messages and reasoning unchanged.** The service records no identity for
  replayed assistant messages or reasoning items, and those items carry no
  call/result pairing, so their existing removal is kept. Recording their item
  ids would not help: replaying clients send them without ids.
- **No state change.** The record is already kept, so no request, response or
  continuity state type changes.
- **HTTP bridge untouched.** `_trim_http_bridge_previous_response_input_items`
  only treats items with a response-output marker (`id` or `status`) as replay.
  It is a different heuristic and outside this change.

## Failure modes

- **Replay the service did not observe.** The replayed tool call is forwarded
  with its output; assistant message and reasoning items are still removed.
- **Output item events missing.** A call whose `response.output_item.done` never
  arrived is not in the record and is forwarded.
- **Client reuses a recorded `call_id` with the same type for a new call.** It
  is treated as replay of the recorded call, which is what its output then
  refers to.
- **Continuity state retired or replaced.** With no matching
  `last_completed_response_id`, no call is proven and none is removed.

## Example

The previous response `resp_a` emitted `function_call` `call_previous`. The app
then continues from `resp_a` with an injected call/result pair ahead of the tool
output:

```json
[
  {"type": "custom_tool_call", "call_id": "call_injected", "name": "visualization_state", "input": "{}"},
  {"type": "custom_tool_call_output", "call_id": "call_injected", "output": "{}"},
  {"type": "function_call_output", "call_id": "call_previous", "output": "ok"}
]
```

Before this change the upstream received only the two outputs, and the
`call_injected` output had no call. Now it receives all three items. A client
that instead replays `resp_a`'s reasoning, assistant message and `call_previous`
call before that output still sends the upstream only the output.

## Compatibility and rollback

- **Compatibility.** No data, schema, setting or API change. Continuations of
  responses this process did not observe now forward their leading tool calls.
- **Rollback.** Revert the change's commit. Nothing persistent has to be undone.
