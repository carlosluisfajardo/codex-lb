## ADDED Requirements

### Requirement: Stored credit reserve gate

The selector SHALL exclude an account with stored credits when either standard usage window is at least 95% used or the applicable long window is unknown. This exclusion SHALL precede routing strategies, sticky selection, quota bypasses, and error-backoff fallback. Last-known credit evidence SHALL survive quota-block freshness filtering. An account with stored credits SHALL never serve additional-quota traffic, even below the standard reserve margin.

#### Scenario: Quota bypass cannot spend the reserve
- **GIVEN** an account has stored credits and a standard window is 95% used
- **WHEN** a request bypasses ordinary quota status
- **THEN** the account is excluded

#### Scenario: Scoped usage cannot erase stored credit evidence
- **GIVEN** standard rows show stored credits and an additional quota is available
- **WHEN** the selector replaces usage rows for additional-quota traffic
- **THEN** the account is excluded before replacement

### Requirement: Retained session reserve fence

A standard account-state build SHALL publish its reserve-gate verdict to the existing retained-session availability fence. Additional-quota state builds SHALL NOT clear that verdict. A later standard build proving recovery SHALL clear only the reserve verdict, preserving an independent pause fence.

#### Scenario: Retained session is fenced after a standard gate verdict
- **WHEN** a standard state build finds an account at the reserve margin
- **THEN** retained-session availability reports it unavailable
