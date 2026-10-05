## ADDED Requirements

### Requirement: Preserve reviewed sibling revisions during integration
The combined release SHALL preserve the contents and parent of
`20261005_000000_add_account_lifecycle_preferences` and
`20261005_000000_add_dashboard_keyless_priority_service_tier`, and SHALL join
them with `20261005_010000_merge_account_lifecycle_and_keyless_priority`.

#### Scenario: Combined upgrade and rollback
- **WHEN** the combined candidate upgrades a database at the common release parent
- **THEN** there SHALL be one head, both additive schemas SHALL be installed,
  existing accounts and unrelated settings SHALL be preserved, and priority SHALL
  default false
- **AND** candidate-aware downgrade to the common parent and re-upgrade SHALL work
  without stamping unknown revisions

### Requirement: Keep the timestamp collision guard narrow
The author-time guard SHALL accept the exact frozen sibling pair only with its
named merge and unchanged common parent. It SHALL reject an additional collision,
a missing or incorrect merge, or a changed parent.

#### Scenario: Unqualified timestamp collision
- **WHEN** another revision shares the slot or the preserved pair has no valid merge
- **THEN** topology validation SHALL report a timestamp collision
