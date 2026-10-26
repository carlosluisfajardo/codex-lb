# Prewarm replay proof

## Scope

Direct Responses WebSocket only. A client full resend that names `previous_response_id` may keep a fresh body without the anchor for the existing pre-acceptance recovery paths (close before `response.created`, or an anchor rejection). Codex opens a session with an empty `generate: false` prewarm and sends its first turn as that anchor plus only new items, so the prewarm is the only carrier of the tools, developer instructions and history the turn depends on. The normative requirement is `specs/responses-api-compat/spec.md`.

## Decisions

- Proof comes from preparation provenance, not fingerprint presence: a prewarm sent with the client's own anchor inherited history that its own input cannot prove, so it records an explicit no-proof entry instead of a fingerprint. A proxy-injected session anchor keeps the client's complete unanchored input, so it still proves.
- Proofs are keyed by the downstream-visible ID (`replay_downstream_response_id or response_id`); a replay's hidden upstream ID is never reachable by the client.
- Each continuity session keeps up to 16 proofs, oldest first. Eviction sets a sticky flag that makes anchors the session cannot classify fail closed, so a lost proof never reverts to the permissive unknown-anchor fallback. Sessions that never evict keep that fallback unchanged.
- The prewarm comparison accepts equality with the complete input. The generic stored-prefix helper keeps its stricter "more items than stored" rule for its other callers.

## Failure modes

- A follow-up that cannot be proven complete loses only transparent recovery: the client receives the existing retryable continuity failure and resends.
- After an eviction, recovery for unknown anchors in that session is refused even when the anchor was not a prewarm.

## Example

A prewarm sends `[additional_tools, developer rules]` without an anchor and completes as `resp_P` with zero output tokens. A follow-up names `resp_P` with `[environment, question]`; the socket closes before `response.created`. The follow-up's input does not begin with the prewarm input, so no fresh body is retained and the client gets `stream_incomplete` instead of a turn answered without tools or rules. A follow-up with `[additional_tools, developer rules, question]`, or exactly `[additional_tools, developer rules]`, is replayed without the anchor because it carries the whole prewarm context.
