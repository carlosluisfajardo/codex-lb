## ADDED Requirements

### Requirement: Catalog-scoped ultra effort floor

After model aliases and API-key enforcement, Responses and compact requests whose resolved model advertises `ultra` in the catalog snapshot SHALL use at least `ultra`, serialized by the existing wire alias as `max`. Requests for other models or without a catalog snapshot SHALL retain existing effort handling. The floor SHALL preserve model, service tier, and reasoning summary.

#### Scenario: Lower enforced effort is raised
- **GIVEN** an API key enforces low effort on a model advertising ultra
- **WHEN** request policy normalizes the request
- **THEN** the upstream effort is max
- **AND** the service tier is unchanged

## MODIFIED Requirements

### Requirement: API-key reasoning allowlists reject disallowed explicit efforts

When an authenticated API key has a non-null
`allowedReasoningEfforts` policy, the proxy MUST derive the client-selected
effort from an explicit `reasoning.effort` or a supported model alias before
performing wire-level normalization. Policy values remain exact client-plane
choices: `xhigh` does not authorize `high`, and `ultra` does not authorize
`max`, even where their downstream wire forms coincide. If the client-selected
effort is not in the policy, the
proxy MUST reject the request before quota reservation, account or source
selection, or upstream dispatch. The rejection MUST use HTTP 403, OpenAI error type `permission_error`, code
`reasoning_effort_not_allowed`, and parameter `reasoning.effort`.

The policy MUST apply to Responses, compact Responses, and WebSocket Responses
requests. Its WebSocket error event MUST preserve the same error code and
parameter. It MUST evaluate the client-plane effort before existing
unsupported-effort fallback and `ultra` to `max` upstream-wire aliasing. A
request that omits an effort MUST retain existing default or API-key-enforced
behavior unless the catalog-scoped `ultra` floor applies. Where that floor
applies, it MUST take precedence after allowlist authorization and API-key
enforcement, including for omitted or lower enforced efforts, and use at least
`ultra`, serialized upstream as `max`. Models that do not advertise `ultra`, or
requests without a catalog snapshot, MUST retain existing effort handling.
Applying the policy more than once to the same request, including across a
signed internal HTTP bridge hop, MUST be idempotent and MUST NOT re-authorize
an already-normalized wire value as though it were the original client choice.
Before source-routed Responses traffic is forwarded, accepted reasoning
aliases MUST be aligned with the authorized canonical `reasoning.effort` or
removed so a conflicting alias cannot select a disallowed effort upstream.
Blank alias strings MUST be treated as absent and MUST NOT mask a later
effort-bearing alias during authorization.
Disabled aliases MUST likewise be treated as inactive rather than masking a
separate enabled reasoning alias.
Provider reasoning metadata MUST be merged with enabled controls before effort
authorization instead of masking their implicit `medium` effort.
When no reasoning policy is active, source egress MUST retain existing
provider-shaped reasoning controls and their source-specific fields.
An allowlist MUST also preserve provider-shaped controls that select no effort;
their unrelated fields do not participate in effort authorization.

#### Scenario: Reject max before upstream dispatch

- **GIVEN** an API key with
  `allowedReasoningEfforts: ["minimal", "low", "medium", "high", "xhigh"]`
- **WHEN** a Responses request explicitly supplies `reasoning.effort: "max"`
- **THEN** the proxy returns `403` with code `reasoning_effort_not_allowed`
- **AND** no API-key quota reservation or upstream request is created

#### Scenario: Alias effort is evaluated as the client-selected value

- **GIVEN** an API key with `allowedReasoningEfforts: ["low", "medium"]`
- **WHEN** a client sends the model alias `gpt-5.6-sol-xhigh`
- **THEN** the proxy rejects the request with code `reasoning_effort_not_allowed`
- **AND** does not forward a request upstream

#### Scenario: Omitted effort remains compatible without a catalog floor

- **GIVEN** an API key with `allowedReasoningEfforts: ["low", "medium"]`
- **AND** the resolved model does not advertise `ultra` in the catalog snapshot
- **WHEN** a Responses request omits `reasoning.effort` and uses no effort alias
- **THEN** the proxy does not add or replace a reasoning effort
- **AND** the request continues through the existing route

#### Scenario: Catalog floor applies after an allowlist accepts omitted effort

- **GIVEN** an API key with `allowedReasoningEfforts: ["low", "medium"]`
- **AND** the resolved model advertises `ultra` in the catalog snapshot
- **WHEN** a Responses request omits `reasoning.effort` and uses no effort alias
- **THEN** the allowlist accepts the omitted client effort
- **AND** the catalog floor sets `ultra` after API-key policy handling
- **AND** the upstream wire effort is `max`

#### Scenario: Effort-less provider controls remain compatible

- **GIVEN** a source-routed model and an API key with
  `allowedReasoningEfforts: ["low"]`
- **WHEN** a Responses request supplies
  `thinking: {"type": "adaptive", "budget_tokens": 2048}` without an effort
- **THEN** the source receives the original `thinking` object

#### Scenario: Source-routed conflicting alias cannot override policy

- **GIVEN** a source-routed model and an API key with
  `allowedReasoningEfforts: ["low"]`
- **WHEN** a Responses request supplies `reasoning.effort: "low"` and
  `thinking: "max"`
- **THEN** the source receives the canonical `reasoning.effort: "low"`
- **AND** it does not receive the conflicting `thinking` alias

#### Scenario: Blank alias cannot hide a disallowed effort

- **GIVEN** a source-routed model and an API key with
  `allowedReasoningEfforts: ["low"]`
- **WHEN** a Responses request supplies `reasoningEffort: " "` and
  `thinking: "max"`
- **THEN** the service returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Disabled alias cannot hide an enabled effort

- **GIVEN** a source-routed model and an API key with
  `allowedReasoningEfforts: ["low"]`
- **WHEN** a Responses request supplies `thinking: "disabled"` and
  `enable_thinking: true`
- **THEN** the service evaluates the enabled alias as `medium`
- **AND** returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Provider metadata cannot hide an enabled effort

- **GIVEN** a source-routed model and an API key with
  `allowedReasoningEfforts: ["low"]`
- **WHEN** a Responses request supplies
  `thinking: {"summary": "auto", "enabled": true}`
- **THEN** the service evaluates the enabled control as `medium`
- **AND** returns `403` with code `reasoning_effort_not_allowed`
- **AND** the source receives no request

#### Scenario: Effort-less provider control survives beside an allowed effort

- **GIVEN** a source-routed model and an API key with
  `allowedReasoningEfforts: ["low"]`
- **WHEN** a Responses request supplies `reasoning.effort: "low"` and
  `thinking: {"type": "adaptive", "budget_tokens": 2048}`
- **THEN** the source receives both the authorized canonical effort and the
  original `thinking` object
