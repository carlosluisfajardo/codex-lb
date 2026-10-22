# Context

This is the bounded LB15 source queue from the operator's LB14 readiness handback: DISPATCH-1/SEL-2, SEL-1 and REG-1. It is not a live regression or a swap request.

A delta-only continuation may be retried on its owner after upstream explicitly rejects its anchor, subject to the existing no-output/no-prior-replay guards. Its retained send count must not erase that established admission decision when the same owner temporarily lacks a local stream slot. Conversely, a generic ambiguous attempted send must remain final.

A public capacity code can coexist with positive cooldown/backoff provenance. The public code must remain compatible while the existing independent full-history proof decides account-neutral replay. Terminal, unknown and file-pinned failures gain no authority from this change.

A creator reservation is owned before planned eviction starts. Cancellation during eviction or the following registry-lock acquisition must settle only that reservation, without mutating a successor or a handoff. Detached resources retain their existing cleanup owner.

The outward HTTP startup path deliberately holds an uncommitted response while capacity is unavailable, preserving a terminal HTTP 429 if its budget expires. This change reuses that path and does not promise emitted SSE keepalives before HTTP startup commits; established capacity keepalive behavior remains covered on the streaming paths that emit it.
