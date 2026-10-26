## Why

An empty prewarm can carry developer instructions, tools and history that a later anchored delta omits. Treating that delta as a complete resend permits a retry to remove the anchor and lose context. The upstream correction needs complete-input provenance, protection for earlier prewarm anchors, and recovery for an exact complete resend.

## What Changes

- Track replay proof for observed empty prewarm anchors separately from completed user turns.
- Derive complete-input proof from request preparation; a fingerprint of an anchored delta is insufficient.
- Retain a safe classification when another prewarm completes, and preserve the downstream-visible response ID through an upstream replay.
- Allow a proven complete resend whose input exactly equals the full prewarm context, while retaining tool self-containment and existing retry/ownership fences.
- Pin the composition with real route/preparation regressions and protective mutations.

## Impact

The change is confined to WebSocket continuity proof and its tests/specification. It adds no setting, schema, durable history, account relocation or retry budget. The already-delivered replacement artifact remains unchanged; this is a separate source correction.
