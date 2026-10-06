## MODIFIED Requirements

### Requirement: Non-message system and developer input items are preserved

When normalizing Responses or compact request `input`, the service MUST only
hoist only `system`-role instruction messages — items
whose `type` is omitted or `"message"` — into the top-level `instructions`
field. Any `system`/`developer`-role input item carrying any other `type`
value, including item types the service does not model, MUST be forwarded
upstream unchanged and in its original input position. This preservation MUST
hold both when the request is validated and when the request is serialized for
upstream delivery, and it exempts the item from input sanitization: keys such
as `reasoning_content`, `reasoning_details`, `tool_calls`, and `function_call`
MUST NOT be stripped from a preserved item. When compact requests exceed the
upstream input budget and
the service trims the input middle, preserved non-message `system`/`developer`
items MUST be treated as trim anchors and retained in the trimmed payload
rather than replaced by the trim marker. When a non-message
`system`/`developer` item is preserved and the request carries no top-level
`instructions` and no hoistable instruction messages, the service MUST default
`instructions` to the empty string so the request still validates and
forwards. Requests whose input contains an `additional_tools` item remain
governed by the Responses Lite rule that leaves the entire input array and
top-level `instructions` unchanged.

#### Scenario: unknown non-message developer input item survives normalization

- **WHEN** a Responses or compact request `input` contains a typed, non-message
  item such as `{"type": "future_directive", "role": "developer", ...}`
  alongside developer instruction messages and user messages
- **THEN** the developer instruction messages remain in `input` with their role and content intact
- **AND** the `future_directive` item remains in `input` unchanged, in its
  original position
- **AND** the upstream-serialized payload retains the item unchanged

#### Scenario: preserved directive keeps reasoning and tool-call keys

- **WHEN** a Responses or compact request `input` contains a typed,
  non-message `system`/`developer` item carrying keys the interleaved
  reasoning sanitizer strips from message items (such as
  `reasoning_content`, `reasoning_details`, `tool_calls`, or `function_call`)
- **THEN** the item is retained byte-identical after validation
- **AND** the upstream-serialized payload retains the item byte-identical

#### Scenario: directive-only request without instructions still validates

- **WHEN** a Responses or compact request omits top-level `instructions` and
  its `input` contains only a typed, non-message `system`/`developer` item
  (such as `{"type": "future_directive", "role": "developer", ...}`) alongside
  user messages
- **THEN** the request validates with `instructions` defaulted to `""`
- **AND** the directive item remains in `input` unchanged, including in the
  upstream-serialized payload

#### Scenario: preserved directive survives compact input trimming

- **WHEN** a compact request is large enough to trigger upstream input
  trimming and its input middle contains a typed, non-message
  `system`/`developer` item such as
  `{"type": "future_directive", "role": "developer", ...}`
- **THEN** the trimmed upstream payload retains the item unchanged
- **AND** the item is not replaced by the trim marker

#### Scenario: typeless system messages keep hoisting behavior

- **WHEN** an OpenAI-compatible client sends `input` containing
  `{"role": "system", "content": "sys"}` without a `type` field
- **THEN** that item is hoisted into `instructions` as before

## ADDED Requirements

### Requirement: Developer messages remain in inherited Responses input

For Responses and compact normalization, the service MUST preserve developer-role messages in input in their original order, retaining their normalized content and role rather than moving their text to top-level instructions. When such a message is present and instructions is omitted, the service MUST default instructions to the empty string. Explicit instructions MUST remain unchanged except for existing system-message hoisting. Ordinary system messages MUST continue to be hoisted in order after explicit instructions. These rules MUST hold at validation and serialization. Responses Lite requests containing additional_tools MUST retain their existing input and instructions behavior.

#### Scenario: Chained request source retains developer context
- **WHEN** an initial Responses input contains developer reference data, a system instruction and a user request
- **THEN** its forwarded input still contains the developer reference data in its original relative order
- **AND** the system instruction is merged after explicit instructions
- **AND** a subsequent request with previous_response_id retains its own instructions and input without synthesizing inherited provider state

#### Scenario: Developer input without explicit instructions validates
- **WHEN** a Responses or compact request contains developer messages without top-level instructions
- **THEN** instructions defaults to the empty string
- **AND** developer messages remain in forwarded input with their role and normalized content
