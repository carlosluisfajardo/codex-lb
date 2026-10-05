## 1. Specification

- [x] 1.1 Add the `account-lifecycle-metadata` capability delta with testable
  requirements for exact-id-and-row keying, declared precision, manual cancellation,
  revision compare-and-set, orphan retention, routing independence, private
  backup and restore, the additive migration and the account detail panel.
- [x] 1.2 Record rationale, failure modes, an example, compatibility and the
  rollback procedure in `context.md`.

## 2. Storage

- [x] 2.1 Add the `account_lifecycle_preferences` model with no foreign key, keyed
  by account id plus the account row's incarnation digest.
- [x] 2.2 Add one additive Alembic revision on the release head
  (`20260912_000000_merge_thread_cache_and_bridge_retirement_heads`), with a
  guarded upgrade and a downgrade that drops only the new table.

## 3. API and backup

- [x] 3.1 Validate civil dates, times and timezones strictly and keep accepted
  values verbatim.
- [x] 3.2 Add `GET` and `PUT /api/accounts/{account_id}/lifecycle` with revision
  compare-and-set and 404 for absent or pending-deletion accounts.
- [x] 3.3 Add `codex-lb account-lifecycle export` (atomic, owner-only,
  no overwrite) and `restore` (snapshot compare-and-set, one transaction).

## 4. Dashboard

- [x] 4.1 Add the lifecycle section to the account detail panel with the read
  view, the current routing policy and its truthful description.
- [x] 4.2 Add Edit with Save and Cancel for write-capable operators. Reload
  on a conflict.
- [x] 4.3 Add `en`, `ko` and `zh-CN` copy and mock handlers.

## 5. Regression coverage

- [x] 5.1 Value validation unit tests (calendar, time, timezone, precision shape).
- [x] 5.2 API tests: absent, saved, cleared, reload from a separate process,
  invalid input, conflict, exact id versus a new id with the same email,
  deletion with a re-created id starting empty, in-place supersede, manual
  cancellation, permissions, routing policy and settings version untouched.
- [x] 5.3 Backup tests: private atomic export, no overwrite, round trip, stale
  snapshot, orphans, restore after a schema rollback never reusing a revision.
- [x] 5.4 Migration tests: upgrade, downgrade and upgrade on a synthetic
  database with a single head.
- [x] 5.5 Dashboard tests: read rendering, Save payload, Cancel, read-only.

## 6. Validation

- [x] 6.1 Ruff, ty, migration topology against the release base and drift
  checks on the final bytes. A fork against a newer upstream main is handled
  by the carry-forward steps in `context.md`.
- [x] 6.2 Frontend lint, typecheck, focused tests and build.
- [x] 6.3 `openspec validate add-account-lifecycle-metadata --strict` and
  `openspec validate --specs`.
