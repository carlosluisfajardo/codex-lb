## ADDED Requirements

### Requirement: Upstream wire capture is default-off and private

The proxy MUST provide an opt-in upstream wire capture controlled by the T4 setting `wire_capture_file`. While the setting is unset, the proxy MUST NOT open a capture sink and MUST NOT allocate capture trace configurations, registries or notes, and HTTP client sessions MUST be constructed exactly as without the feature. When the setting is set, the capture MUST create a new file at the configured absolute path with `O_CREAT|O_EXCL|O_NOFOLLOW` and mode `0600`. The file's parent directory MUST be owned by the effective user and MUST have no group or other permission bits. If any of these conditions fails, capture MUST stay disabled. Every record MUST be one JSON line of at most 2048 bytes, the number of records and of in-memory tracking entries MUST be bounded, and any capture failure MUST permanently disable capture for the process without raising into or changing the outcome of the request being observed. Before each write the capture MUST verify that its descriptor still refers to the file it created, and MUST NOT write to or close a descriptor that no longer does. The first record MUST be `capture_open`, written when the shared HTTP client is built at startup. It proves only that the sink opened for that process (process id and application version); a missing file can mean unsupported configuration, a rejected sink path or a capture failure.

#### Scenario: Capture is unset

- **WHEN** `wire_capture_file` is unset
- **THEN** no capture file is created and HTTP client sessions carry no capture trace configuration

#### Scenario: Unsafe sink path

- **WHEN** `wire_capture_file` is relative, names an existing file or a symlink, or its parent directory is not owned by the effective user or has group/other permission bits
- **THEN** no record is written and capture stays disabled for the process
- **AND** requests are served unchanged

#### Scenario: Sink descriptor reused by another resource

- **WHEN** the capture descriptor has been closed and its number reused by another file or socket
- **THEN** no capture record is written to that descriptor and it is not closed by the capture
- **AND** capture is disabled for the process

#### Scenario: Capture failure during a request

- **WHEN** writing a capture record fails while an upstream request is in flight
- **THEN** the request completes with the outcome it would have had without capture
- **AND** no further capture record is written by that process

### Requirement: Upstream wire capture records only validated metadata

Every capture value MUST be validated before it is written. Service tier, request kind, transport, configured upstream transport, HTTP method, route mode, request status and URL scheme MUST come from closed sets. URL paths MUST be reduced to the fixed path class `codex_responses` or not recorded. Identifiers MUST be written only in canonical lowercase UUID form, bridge reference form (`ws_` or `http_prewarm_` followed by 32 lowercase hex characters) or response form (`resp_` followed by 1-128 ASCII alphanumerics). A value outside its allowed form MUST be written as `null` with a fixed state (`absent`, `unrecognized`, `noncanonical` or `not_observed`), and the rejected value MUST NOT be stringified, truncated, hashed or serialized in any form. Only exact `str` or `int` values are accepted; subclasses and other objects are rejected without invoking their methods. The capture MUST NOT write headers (it reads only the `tier=` token of `x-codex-routing-hint`), URL hosts, ports or queries, account or API-key identifiers, error messages, exception messages, or request, frame or event payload text.

#### Scenario: Sentinel inside an allowed field

- **WHEN** a recorded field such as the service tier, conversation id, request-log response id or routing-hint tier carries an arbitrary string, a container, or an object with a custom `__str__`/`__repr__`
- **THEN** the record writes `null` with the field's fixed rejection state
- **AND** the arbitrary content does not appear anywhere in the capture file

### Requirement: Upstream wire capture labels provenance

Each recorded status, tier and identifier MUST carry the origin that produced it.

- **HTTP status.** An aiohttp hop status MUST be labelled as the final response status of that request (intermediate redirect responses are only counted).
- **Websocket handshake 101.** A handshake status of 101 MUST be attributed to the websockets library contract only for a connection whose route mode is `direct` while native egress discovery is cached as absent. A routed connection's status MUST come from a matching same-scope aiohttp upgrade trace or be `null` with an unknown origin, and a connection that may be native MUST be `null` with an unknown origin.
- **Routing-hint tier.** A directly traced HTTP hop MAY record its own validated routing-hint tier. The bridge send seam MUST NOT infer an opener hint from the first observed sender, because that sender may differ from the connection creator. Without actual opener correlation the bridge hint MUST remain `not_observed`; sends retain the connection reference for the known handshake observation.
- **Pre-send payload.** The outgoing payload record MUST be labelled `pre_send`.
- **Bridge send.** A bridge frame record MUST be written only after the local send completed and MUST be labelled as local send completion, not as provider acknowledgement. Its tier MUST come from the prepared-dict note only for the first send attempt of that bridge request; any other send MUST record the tier as `not_observed`.
- **Request-log response id.** The request-log response id MUST be labelled as caller-dependent with an unresolved origin, whatever its prefix.
- **Bridge upstream response id.** The bridge upstream response id MUST come from the bridge request state at detach, together with whether a replay alias was present.
- **Normalized returned tier.** The service's normalized `None` MUST be labelled `not_observed`, because both an absent and an invalid upstream value can normalize to it. It MUST NOT prove upstream tier omission.

#### Scenario: Request-log id with a response prefix

- **WHEN** the request-log hook receives an id that starts with `resp_`
- **THEN** the record keeps its origin as unresolved and does not label it as the upstream response id

#### Scenario: Retry of a bridge request

- **WHEN** a bridge request is sent a second time on the same bridge request state
- **THEN** that send record carries `outgoing_service_tier` `null` with state `not_observed`

### Requirement: Upstream wire capture classification is conservative

The capture classifier MUST report a tier verdict (`PRIORITY_SENT_DEFAULT_REPORTED`, `PRIORITY_SENT_PRIORITY_REPORTED`, `PRIORITY_SENT_OTHER_TIER_REPORTED`, `PRIORITY_SENT_TIER_OMITTED` or `PRIORITY_NOT_SENT`) only when all of the following hold:

- exactly one eligible normal attempt exists for the labelled canonical conversation id;
- local send of that attempt is proven, with matching canonical ingress, conversation and bridge reference identifiers;
- the upstream response id is proven independently of the caller-dependent request-log alias;
- the outgoing tier was observed at origin;
- the request status is `success`;
- for the HTTP bridge, the bridge reference, attempt count, connection handshake, positional response matching and replay-alias state are unambiguous.

A bridge connection with multiple captured sends MUST remain `UNKNOWN`/`PARTIAL` without an explicit frame-to-response mapping, even if the other send belongs to a different conversation or sequential reuse. A queue-length snapshot alone MUST NOT prove that mapping.

Every other case MUST yield `UNKNOWN` with fixed reason codes and completeness `PARTIAL`, and MUST NOT yield `PRIORITY_NOT_SENT` or `PRIORITY_SENT_TIER_OMITTED`. A direct raw-websocket frame with only a pre-send record and a handshake status MUST be `UNKNOWN`. A direct-path upstream response id MUST be reported unresolved.

#### Scenario: Missing tier note

- **WHEN** the single bridge send record carries `outgoing_service_tier` state `not_observed`
- **THEN** the classifier reports `UNKNOWN` with completeness `PARTIAL`

#### Scenario: Two eligible attempts

- **WHEN** two normal request-log rows exist for the labelled conversation and no explicit attempt-to-response mapping exists
- **THEN** the classifier reports `UNKNOWN` with completeness `PARTIAL`

#### Scenario: Missing or mismatched response correlation

- **WHEN** a send belongs to another ingress or conversation, lacks local send completion, or the upstream response id is unresolved
- **THEN** the classifier reports `UNKNOWN` with completeness `PARTIAL`
- **AND** individual observed tiers and status remain available without becoming a complete request verdict
