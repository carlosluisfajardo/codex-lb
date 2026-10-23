## Why

The source audit at divergent main b8512db1 reports eleven authentication, forwarding, selection, usage and reset-claim defects. This candidate integrates on the delivered maintenance lineage at 1f2f58b78ab5901ba820f5e6d66ecd7b1fdeb7e4 (tree 089742fcb0ad1c27fa0cd5813b37427cb8eb9a71); each report is reconciled against that source before correction. The related reserve-controller review supplies six overlapping LB contracts for selected resets, routing ownership, held-account freshness and exact pin cleanup. These source changes form the single assigned LB remediation candidate; they do not operate or replace the live LB.

## What Changes

- Authenticate trusted dashboard headers against the preserved socket peer, strip local authentication cookies at outbound boundaries, and reject foreign browser origins before keyless WebSocket acceptance.
- Fence stale credential failures and runtime synchronization; preserve secondary-only credits and unavailable-owner eligibility.
- Establish hard sticky ownership atomically and refresh soft bindings only while the observed owner still matches.
- Propagate SQLite reset-claim ownership loss to the body, fence consume admission, and clean up a committed claim when acquisition is cancelled.
- Bind the first dashboard reset selection to caller expectations; expose held-account refresh freshness, conditional routing/state transitions and exact snapshot-bound pin deletion.
- Return the persisted held-account refill reading, let expected-target consume reach its authoritative fetch past a stale or absent cache, add an explicit fresh reset-credit read, and refuse caller expectations on the untargeted usage-reset consume.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `admin-auth`: socket-peer dashboard trust and upstream cookie isolation.
- `responses-api-compat`: keyless WebSocket browser-origin admission on equivalent paths.
- `account-routing`: versioned runtime synchronization, conditional state/policy changes, reserve holds and continuity eligibility.
- `usage-refresh-policy`: credential-aware stale-failure refusal, secondary credit snapshots and successful per-window freshness while held.
- `sticky-session-operations`: atomic first-owner publication, conditional same-owner refresh and exact snapshot-bound cleanup.
- `rate-limit-reset-credits`: caller-bound initial selection and claim-loss/cancellation fencing.

## Impact

Changes are limited to the existing app layers, hermetic tests and owning OpenSpec artifacts. Existing repository conditional primitives and forced refresh are reused where sufficient. The assigned coherent candidate may exceed the normal PR size guideline because all eleven audit items and the six overlapping supplier contracts must be integrated together; it adds no controller, framework, dependency or configuration setting. The historical audit ranking is attributed, including the Low soft-affinity dissent and unsigned revised ranking.

## Non-goals

No live routing, reserve spending, reset redemption, refresh, account operation, credential access, service restart or replacement. No at-most-once external consume or upstream double-charge guarantee. Ordinary two-process SQLite startup is outside the supported same-process lease-loss reproduction. Final independent review, risky Claude review, CI and replacement delivery remain Setup B-owned; only the operator may replace the live LB with Codex closed.
