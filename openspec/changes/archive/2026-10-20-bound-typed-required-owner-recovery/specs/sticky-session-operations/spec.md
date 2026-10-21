## ADDED Requirements

### Requirement: Typed required-owner selection failures

Selection SHALL carry a request-local typed reason for a failed required owner through core selection, hard-affinity remapping, account selection and service selection. Legacy constructors SHALL default to unknown. Cooldown, error backoff and proven local contention SHALL be distinguishable from quota, rate limit, expired authentication, paused or deactivated state, policy refusal, absence, exclusion and unknown failure. Terminal-for-recovery or unknown eligibility SHALL take precedence over local contention. Public selection error envelopes SHALL remain compatible. Hard-affinity remapping SHALL preserve the reason and reset metadata.

#### Scenario: Mixed quota and capacity blockers
- **GIVEN** a required owner is quota-blocked and locally at capacity
- **WHEN** account selection fails
- **THEN** its typed reason is quota, never temporary local contention
- **AND** no alternate owner is selected

#### Scenario: Mixed authentication and cooldown blockers
- **GIVEN** a required owner has expired authentication and an active cooldown
- **WHEN** account selection fails
- **THEN** its typed reason is authentication, never temporary cooldown

#### Scenario: Hard affinity preserves failure provenance
- **WHEN** a hard owner fails core selection with a typed reason and reset time
- **THEN** hard-affinity error remapping preserves both values

#### Scenario: Opportunistic policy error remapping
- **GIVEN** core selection refuses a required owner with the typed policy reason
- **WHEN** account selection maps the refusal to the opportunistic burn-window public envelope
- **THEN** the typed reason SHALL remain policy

#### Scenario: Unknown legacy failure
- **WHEN** a selector returns a failure using a legacy constructor without a reason
- **THEN** the reason is unknown and grants no temporary recovery wait
