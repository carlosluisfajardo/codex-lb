## Why

Subscription streams can retain a two-hour silence allowance. Direct downstream WebSocket keepalives also rearm the upstream idle deadline, and routed HTTP loses the response-header timeout when it passes only a total timeout. Bounded silence must remain distinct from legitimate long generation.

## What Changes

- Default the existing stream idle allowance to 600 seconds while preserving the 7200-second overall stream budget and explicit operator overrides.
- Preserve a WebSocket upstream receive deadline across locally emitted keepalives; renew it on received upstream frames.
- Carry the existing connect/header/body timeout contract through routed subscription HTTP.
- Remove timeout-only first-event HTTP account failover: silence is delivery-ambiguous.
- Preserve existing HTTP bridge response-start watchdogs, ownership fences, terminal errors and settlement order; qualify them with virtual-time fixtures.

## Capabilities

### Modified Capabilities
- `responses-api-compat`: bounded subscription silence without a first-token deadline.
- `outbound-http-clients`: phase timeout propagation across direct and routed HTTP.

## Impact

Only existing subscription timeout settings, client and WebSocket receive seams, focused tests and owning specifications. No new settings, dependencies, migration, retry entitlement or live operation.
