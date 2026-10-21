## Why

Operator verification of maintenance merge a9e5819 confirmed that the new owner window delays and blocks previously proven full-history failover, shortens local-capacity waits, and conflicts with shared creation admission. Root explicitly changes the exhausted-window policy for eligible pre-dispatch requests. Historical archived requirements remain historical.

## What Changes

- Prefer the existing positive durable full-resend proof over waiting for a temporarily unavailable owner before dispatch.
- Keep local contention on the existing outer request-deadline capacity wait.
- Bound joiners by creation/request deadlines without letting a waiter cancel or evict another request's creation.
- Apply the recovery-inheritance fence only once the creator actually enters owner recovery, including a transition after the join begins.
- Reduce repeated selector passes within the same 30-second recovery window.

## Capabilities

### Modified Capabilities
- `responses-api-compat`: guarded replay priority, bounded owner recovery and healthy session sharing.
- `proxy-admission-control`: creation waits follow the creator/request deadline and preserve creator ownership.

## Impact

Owning HTTP bridge selection, registry and streaming seams; focused bridge/selector tests. No dependency, schema, account eligibility, setting, health policy or deployment change.
