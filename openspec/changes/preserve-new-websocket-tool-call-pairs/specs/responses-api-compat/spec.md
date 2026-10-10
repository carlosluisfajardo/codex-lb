## ADDED Requirements

### Requirement: WebSocket continuations remove only tool calls proven to be previous-response replay

When a direct WebSocket `response.create` request carries `previous_response_id`
and a list `input` whose first `function_call_output`, `custom_tool_call_output`
or `apply_patch_call_output` item is preceded only by assistant messages,
reasoning items and tool calls, the service MAY remove the assistant messages
and reasoning items of that prefix before forwarding the request. It MUST
remove a leading `function_call`, `custom_tool_call` or `apply_patch_call` item
only when that item's `call_id` and type equal the `call_id` and type of a tool
call the service proved the response named by `previous_response_id` emitted,
on the same WebSocket continuity state. A tool call is proven only when its
`response.output_item.done` event named that response's id, or arrived without
a response id while that response was the only one the upstream had created on
the connection. A tool call attributed to the response by any other routing
fallback MUST NOT prove replay. The service MUST NOT treat a tool call as replay
because of its item type, the presence of `previous_response_id`, its item `id`,
or a matching output in the same request. Every other leading tool call MUST be forwarded
unchanged and in its original order relative to the forwarded items, so a new
call and its output reach the upstream together. When the prefix contains any
other item, the request MUST be forwarded unchanged. The service MUST NOT
synthesize, duplicate or resend a tool call, a tool output or any side effect
when it keeps or removes these items.

#### Scenario: A new call/result pair on a continuation reaches the upstream whole

- **GIVEN** the response `resp_a` emitted only the `function_call` `call_previous` on this WebSocket continuity state
- **WHEN** the client continues from `resp_a` with a new `custom_tool_call` `call_injected`, its `custom_tool_call_output`, and the `function_call_output` of `call_previous`
- **THEN** the upstream receives those three items unchanged and in that order
- **AND** the same holds when the new call is a `function_call` or an `apply_patch_call`

#### Scenario: A proven replay of the previous response is removed

- **GIVEN** the response `resp_a` emitted the `function_call` `call_previous` on this WebSocket continuity state
- **WHEN** the client continues from `resp_a` with a reasoning item, an assistant message, the `function_call` `call_previous` and its `function_call_output`
- **THEN** the upstream receives only the `function_call_output` of `call_previous`

#### Scenario: A tool call attributed by a pipelined-socket fallback does not prove replay

- **GIVEN** responses `resp_A` and `resp_B` were created on the connection and a third request is still waiting for its `response.created`
- **AND** a `response.output_item.done` event without a response id, carrying the `function_call` `call_from_A`, is routed to that third request, which then completes as `resp_C`
- **WHEN** the client continues from `resp_C` with the `function_call` `call_from_A` and its `function_call_output`
- **THEN** the upstream receives both items unchanged

#### Scenario: An explicit response id proves replay on a multiplexed connection

- **GIVEN** responses `resp_A`, `resp_B` and `resp_C` were created on the connection
- **AND** a `response.output_item.done` event naming `resp_C` carried the `function_call` `call_from_C`
- **WHEN** the client continues from `resp_C` replaying the `function_call` `call_from_C` before its `function_call_output`
- **THEN** the upstream receives only the `function_call_output`

#### Scenario: A response the service did not observe keeps its tool calls

- **WHEN** the client continues from a response the service has no record of on this continuity state, with a reasoning item, an assistant message, a `function_call` and its `function_call_output`
- **THEN** the upstream receives the `function_call` followed by its `function_call_output`

#### Scenario: A matching call id with a different type is not replay

- **GIVEN** the previous response emitted the `function_call` `call_shared`
- **WHEN** the continuation starts with a `custom_tool_call` `call_shared` and its output
- **THEN** both items are forwarded unchanged

#### Scenario: An item id is not call identity

- **GIVEN** the previous response emitted the `function_call` `call_previous`
- **WHEN** the continuation starts with a replayed `function_call` whose `call_id` is `call_previous` and a new `function_call` whose item `id` is `call_previous` but whose `call_id` is `call_new`, followed by both outputs
- **THEN** only the replayed `call_previous` call is removed and the `call_new` call is forwarded before both outputs

#### Scenario: Ordinary continuations are unchanged

- **WHEN** the continuation input starts with a tool output, or carries no tool output, or places a user message before its first tool output
- **THEN** the input is forwarded unchanged
