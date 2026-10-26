## ADDED Requirements

### Requirement: Empty prewarm references require complete replay context proof

A direct Responses WebSocket request anchored to an observed empty prewarm MUST NOT be retried without its anchor unless the retained replay body is proven to contain the prewarm's complete context. An input count or fingerprint of an anchored delta alone MUST NOT prove completeness of inherited context. Completing a later prewarm MUST NOT cause an earlier known prewarm anchor with missing proof to enter a permissive unknown-anchor fallback.

The proof MUST use the response ID visible to the downstream client, including when an upstream replay uses a hidden replacement ID. A proven complete resend containing at least two items MAY exactly equal the complete prewarm input; it MUST retain existing self-containment, owner, pending-request, progress, sequence and retry-budget requirements. Recording this proof MUST NOT mark a prewarm as user-turn progress, account success or a usable previous-response owner.

#### Scenario: A delta after an empty prewarm cannot lose its developer and tool context

- **GIVEN** an empty prewarm carries developer instructions and tools
- **AND** a later request references that prewarm with only new user items
- **WHEN** the upstream closes before accepting the later request
- **THEN** the proxy does not dispatch those new items as a fresh unanchored request
- **AND** the client receives the existing retryable continuity failure

#### Scenario: An anchored prewarm does not certify inherited context from its delta fingerprint

- **GIVEN** a prewarm references an earlier response and supplies only an additional developer item
- **WHEN** a later request repeats that item plus a new user item while referencing the prewarm
- **THEN** the repeated delta alone does not authorize removing the prewarm anchor

#### Scenario: A later prewarm cannot erase protection for an earlier prewarm anchor

- **GIVEN** two empty prewarms complete sequentially with distinct context
- **WHEN** a delta references the first prewarm
- **THEN** the proxy preserves the first anchor's replay-safety classification
- **AND** absent complete proof cannot become permission to replay the delta as a fresh request

#### Scenario: An exact complete resend remains recoverable

- **GIVEN** an unanchored prewarm has proven complete input containing at least two self-contained items
- **WHEN** a later anchored request repeats exactly that complete input
- **THEN** the proxy may retain the complete request without its anchor for the existing bounded recovery path

#### Scenario: Replayed prewarm proof uses the downstream-visible ID

- **GIVEN** a prewarm replay uses a hidden upstream replacement ID
- **WHEN** the client follows the original visible response ID
- **THEN** complete-context proof is associated with the visible ID
- **AND** the existing single visible lifecycle and settlement behavior are preserved
