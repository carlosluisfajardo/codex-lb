## ADDED Requirements

### Requirement: Keyless requests can opt in to the priority service tier

The dashboard settings MUST persist a boolean `keyless_priority_service_tier`, exposed as `keylessPriorityServiceTier` by `GET /api/settings` and accepted by `PUT /api/settings`. It MUST default to `false` for new installs and for rows that existed before the column. A `PUT` that omits the field or sends `null` MUST leave the stored value unchanged.

When the setting is `true`, the proxy MUST forward `service_tier: "priority"` upstream for a request that is authenticated without a proxy API key and routed to a subscription account, when the request's service tier is absent, `null`, blank, `auto` or `default` (case-insensitive). This MUST apply to `/backend-api/codex/responses`, `/v1/responses`, `/v1/chat/completions`, the compact routes and native WebSocket `response.create` frames, including requests that reach upstream through the HTTP session bridge.

The opt-in MUST NOT change:

- a request authenticated with a proxy API key;
- a request whose service tier is any other explicit value;
- a request while the global `prohibit_fast_mode` setting is `true`;
- a request routed directly to a model source;
- the service tier of an owner-forwarded HTTP bridge request on the owner instance.

The catalog fallback for policy-supplied tiers MUST still remove the substituted `priority` when the model catalog says the model does not offer it.

HTTP requests MUST read the opt-in and global veto from one coherent settings snapshot. A concurrent settings update MUST NOT combine the old row's veto with the new row's opt-in. Native WebSocket keeps its existing connection-level snapshot, and a bridge owner keeps its separate signed-forwarding veto.

#### Scenario: Concurrent atomic settings toggle cannot manufacture permission

- **GIVEN** the settings row changes atomically from opt-in off/veto off to opt-in on/veto on during HTTP routing
- **WHEN** a keyless Responses, chat or compact request has no service tier
- **THEN** it forwards no injected priority, because neither coherent row permits it

#### Scenario: Opt-in off keeps the client tier

- **GIVEN** `keylessPriorityServiceTier` is `false`
- **WHEN** a keyless client sends a Responses request with no `service_tier`, or with `service_tier: "default"`
- **THEN** the upstream request carries the same tier the client sent

#### Scenario: Opt-in on requests priority for a tier left to upstream

- **GIVEN** `keylessPriorityServiceTier` is `true` and `prohibitFastMode` is `false`
- **WHEN** a keyless client sends a Responses request over HTTP or a native WebSocket `response.create` with `service_tier` absent, `null`, `auto` or `default`
- **THEN** the upstream request carries `service_tier: "priority"`

#### Scenario: Only the service tier changes

- **GIVEN** `keylessPriorityServiceTier` is `true`
- **WHEN** a keyless client sends a Responses request with no `service_tier`
- **THEN** the upstream request is identical to the one produced for the same request sent with `service_tier: "priority"`
- **AND** its model and reasoning effort are the ones the client sent

#### Scenario: Explicit client tier is preserved

- **GIVEN** `keylessPriorityServiceTier` is `true`
- **WHEN** a keyless client sends `service_tier: "flex"`
- **THEN** the upstream request carries `service_tier: "flex"`

#### Scenario: Fast Mode prohibition wins

- **GIVEN** `keylessPriorityServiceTier` is `true` and `prohibitFastMode` is `true`
- **WHEN** a keyless client sends a request with no `service_tier` or with `service_tier: "default"`
- **THEN** the upstream request carries no `priority` tier and the client value is forwarded as before

#### Scenario: Keyed requests keep their API-key policy

- **GIVEN** `keylessPriorityServiceTier` is `true`
- **WHEN** a request is authenticated with a proxy API key
- **THEN** its service tier is decided only by that key's policy and the client request

#### Scenario: Catalog fallback still removes an unsupported substituted tier

- **GIVEN** `keylessPriorityServiceTier` is `true`
- **AND** the authoritative model catalog says the requested model does not offer `priority`
- **WHEN** a keyless request with no `service_tier` is routed to a subscription account
- **THEN** the upstream request carries no `service_tier`

#### Scenario: Overflow dispatch still strips the tier

- **GIVEN** a keyless request received the substituted `priority` tier
- **WHEN** subscription-exhaustion overflow dispatches it to an overflow source
- **THEN** the source request carries no `service_tier`

#### Scenario: Owner instance keeps the origin's signed tier

- **GIVEN** an origin instance forwarded a keyless request to the bridge owner with a signed effective tier
- **WHEN** the owner instance processes it
- **THEN** the owner forwards the signed tier unchanged, whatever the owner's own `keylessPriorityServiceTier` value, subject only to the owner's `prohibitFastMode` veto

#### Scenario: Request logs keep requested and upstream-returned tiers apart

- **GIVEN** `keylessPriorityServiceTier` is `true`
- **WHEN** a keyless request with no `service_tier` is forwarded with `priority` and the upstream response reports `service_tier: "default"`
- **THEN** the request log records `requested_service_tier = "priority"`
- **AND** the request log records `actual_service_tier = "default"`
- **AND** the request log records billable `service_tier = "default"`

#### Scenario: Existing installs migrate to the opt-in off

- **GIVEN** a database whose `dashboard_settings` row predates the column
- **WHEN** migrations upgrade it to head
- **THEN** the row has `keyless_priority_service_tier = false` and its other settings are unchanged
- **AND** downgrading to the previous revision removes the column and keeps the row
