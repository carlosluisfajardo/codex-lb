## ADDED Requirements

### Requirement: Empty prewarm references require complete replay context proof

A direct Responses WebSocket request whose `previous_response_id` names an empty prewarm completed in the same continuity session MUST NOT be replayed without that anchor unless its input begins with, or exactly equals, the prewarm's complete input. A prewarm's input counts as complete only when the client sent the prewarm without its own `previous_response_id` and the input carried items; an input count or fingerprint of an anchored prewarm's delta MUST NOT prove inherited context, and a prewarm without input items provides no proof. The proof MUST be associated with the response ID visible to the downstream client, including when an upstream replay completed the prewarm under a hidden replacement ID.

Within the retained bound, completing a later prewarm MUST NOT discard an earlier prewarm's proof or turn that earlier anchor into an unknown one. The service MAY bound the prewarm proofs it retains per session; once one is evicted, an anchor the session cannot classify MUST NOT be replayed without its anchor. Recording this proof MUST NOT mark a prewarm as user-turn progress, account success, or a usable previous-response owner, and MUST NOT relax the existing self-containment, owner, pending-request, visible-output, sequence, or retry-budget requirements.

#### Scenario: A delta after an empty prewarm cannot lose its developer and tool context

- **GIVEN** an empty prewarm carries developer instructions and tools
- **AND** a later `/v1/responses` or `/backend-api/codex/responses` WebSocket request references that prewarm with only new user items
- **WHEN** the upstream closes before accepting the later request
- **THEN** the proxy does not dispatch those new items as a fresh unanchored request
- **AND** the client receives the existing retryable continuity failure

#### Scenario: An anchored prewarm does not certify inherited context from its delta fingerprint

- **GIVEN** a prewarm references an earlier response and supplies only an additional developer item
- **WHEN** a later request repeats that item plus a new user item while referencing the prewarm
- **THEN** the repeated delta does not authorize removing the prewarm anchor

#### Scenario: A later prewarm cannot erase protection for an earlier prewarm anchor

- **GIVEN** two empty prewarms complete sequentially with distinct context
- **WHEN** a delta references the first prewarm
- **THEN** the proxy does not replay the delta as a fresh request
- **AND** a resend that begins with the first prewarm's complete input remains recoverable

#### Scenario: An evicted prewarm proof fails closed

- **GIVEN** more empty prewarms complete in one session than the service retains proofs for
- **WHEN** a request references an evicted prewarm or another anchor the session cannot classify
- **THEN** the proxy does not replay that request without its anchor

#### Scenario: An exact complete resend remains recoverable

- **GIVEN** an unanchored prewarm completed with input containing at least two self-contained items
- **WHEN** a later anchored request repeats exactly that input
- **AND** the upstream closes before accepting it
- **THEN** the proxy replays the complete request without its anchor through the existing bounded recovery path

#### Scenario: Replayed prewarm proof uses the downstream-visible ID

- **GIVEN** a prewarm was accepted under a visible response ID and its replay completed upstream under a hidden replacement ID
- **WHEN** the client sends a delta referencing the visible ID and upstream rejects that anchor
- **THEN** the proxy does not replay the delta without its anchor
- **AND** the client observed one prewarm lifecycle under the visible ID
- **AND** the prewarm records neither account success nor previous-response ownership
