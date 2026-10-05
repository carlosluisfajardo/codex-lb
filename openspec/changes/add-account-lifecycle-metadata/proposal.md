# Add manually entered account lifecycle metadata

## Why

Operators who pool several subscriptions need to know when each one stops or
renews so they can decide which account to spend first. That information is not
in any upstream payload codex-lb receives: a subscription that the owner has
cancelled still reports ordinary weekly and monthly quota windows, and the
reset-credit expiry or the last login date says nothing about when access ends.
Today the operator keeps these dates outside the dashboard, next to an account
list that already carries the one control they act on, the per-account routing
policy.

Guessing the dates is worse than not showing them. A quota window reset, a
reset-credit expiry and a subscription end can fall on the same calendar day
while meaning three different things, and a subscription end that is known
only as a date must not be displayed as an exact instant.

## What Changes

- Store, per exact opaque account id and its account row, three manually
  entered fields: `Ends on`, `Renews on` and an optional cancellation status.
  Each date is either unset, a civil date, or a local date and time with an
  explicitly supplied timezone, and is kept exactly as declared.
- Add `GET` and `PUT /api/accounts/{account_id}/lifecycle` with a per-record
  revision for compare-and-set writes.
- Keep the metadata in its own table with no foreign key, so deleting an account
  leaves an inert, exportable record that no later account sees, even one that is
  given the same id.
- Add a private, host-local backup: `codex-lb account-lifecycle export` writes
  only these fields to an owner-only file, and `codex-lb account-lifecycle
  restore` puts a backup back under a whole-namespace compare-and-set.
- Show and edit the fields in the existing account detail panel, next to the
  account's current routing policy and a plain statement of what that policy
  does and does not do.

## Non Goals

- No inference of any lifecycle field from quota windows, reset credits,
  billing, authentication, login dates or email addresses.
- No change to routing: the lifecycle fields are not read by account selection,
  sticky ownership, retries, service tiers, Fast mode or timeout budgets.
- No per-account selection rank, no "use reset now", no scheduled or automatic
  reset redemption. Those remain separate, later changes.
- No new dashboard page, navigation item, setting or environment variable.

## Capabilities

### New Capabilities

- `account-lifecycle-metadata`: manually entered subscription lifecycle fields
  per exact account id, their API, private backup and restore, and their
  presentation in the account detail panel.

### Modified Capabilities

None. The account routing policy and its control keep their existing behaviour;
the new panel only displays the current value.

## Impact

- Schema: one additive table, `account_lifecycle_preferences`, one Alembic
  revision on the release head
  `20260912_000000_merge_thread_cache_and_bridge_retirement_heads`, no backfill.
  An upgrade changes no existing row and introduces no decision. Carrying it
  onto a newer upstream head follows `context.md`.
- API: two dashboard routes under the existing accounts prefix; existing routes
  and response schemas are unchanged.
- CLI: one `codex-lb account-lifecycle` command group (`export`, `restore`).
- Dashboard: one section in the account detail panel, with copy in `en`, `ko`
  and `zh-CN`.
- Configuration: none. The feature works with zero configuration.
- Compatibility and rollback: see `context.md`.
