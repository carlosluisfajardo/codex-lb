## Why

An empty Codex prewarm can carry developer instructions, tools and history that the next anchored request omits. Empty prewarms were not recorded in direct WebSocket continuity, so the full-resend check treated such a delta as a complete resend; a close or anchor rejection before acceptance then replayed the delta without its anchor and lost that context. The upstream correction (Soju06/codex-lb#2575) records one prewarm slot, but its fingerprint of an anchored prewarm is not proof of inherited context, a later prewarm forgets an earlier one, and its strict prefix check refuses an exact complete resend.

## What Changes

- Record each empty prewarm's replay proof separately from completed turns, under the downstream-visible response ID.
- Treat a prewarm's input as complete only when the client sent it without its own anchor; an anchored prewarm or one without input items provides no proof.
- Keep a bounded set of prewarm proofs per continuity session. A later prewarm keeps earlier proofs; once a proof is evicted, anchors the session cannot classify fail closed.
- Accept a resend that begins with, or exactly equals, the prewarm's complete input, behind the existing self-containment check.
- Pin the composition with real route/preparation regressions and protective mutations.

## Impact

The change is confined to direct WebSocket continuity proof (`support.py`, `websocket/helpers.py`, `websocket/mixin.py`) and its tests/specification. It adds no setting, schema, durable history, account relocation, retry budget or HTTP bridge change. The already-delivered replacement artifact remains unchanged; this is a separate source correction.
