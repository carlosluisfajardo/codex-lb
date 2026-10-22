## ADDED Requirements

### Requirement: First hard ownership is published atomically
Concurrent first selectors of a hard `codex_session` key MUST use insert-if-absent and reconcile to its persisted winner before dispatch. Losers MUST release provisional leases and recheck winner model and caller scope without overwriting ownership.
#### Scenario: Missing-owner reads choose different accounts
- **WHEN** two selectors both read no owner and choose different accounts
- **THEN** one durable owner wins and eligible requests converge on it
#### Scenario: Winner is outside caller scope
- **WHEN** the losing caller cannot use the winner
- **THEN** it fails without dispatching an alternative or retaining a lease

### Requirement: Soft freshness cannot overwrite newer ownership
A soft same-owner freshness write MUST condition on the observed owner and un-abandoned row. It MUST NOT recreate a deleted row, overwrite a newer binding or initialize a stale process seed. Intentional rebinds remain separate.
#### Scenario: Refresh loses to a rebind
- **WHEN** selection observes A and another writer rebinds to B before refresh
- **THEN** B remains owner and no stale A seed is created

### Requirement: Snapshot cleanup binds account and ownership generation
Listing SHALL accept exact `accountId` and expose account ID and ownership generation. Snapshot deletion MUST atomically match submitted key, kind, account ID and generation, process only submitted entries, refuse reserved keys and report changed/missing entries. Ownership upserts and delete/recreate MUST create new generations. Rows without a generation MUST be ineligible.
#### Scenario: Duplicate names do not broaden cleanup
- **WHEN** accounts share an email and one exact account ID is listed
- **THEN** only that account's entries appear
#### Scenario: List-delete races preserve changed and new pins
- **WHEN** selected ownership is rebound or recreated and new pins appear after listing
- **THEN** only unchanged submitted mappings are deleted
#### Scenario: Current generation does not authorize another account
- **WHEN** the submitted account differs despite a current generation
- **THEN** the mapping is preserved and reported changed or missing
