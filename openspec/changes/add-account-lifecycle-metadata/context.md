# Context

Normative requirements live in `specs/account-lifecycle-metadata/spec.md`. This
file explains why the change is shaped this way, how it behaves under failure,
and how to back it out.

## Purpose and scope

Operators record, per account, when its subscription ends or renews and whether
its owner cancelled it. The values are typed in by hand because no upstream
payload codex-lb receives contains them. They are shown next to the routing
policy the operator already uses to decide which account to spend first. They
are notes: nothing in the proxy reads them.

## Decisions and the alternatives rejected

- **Exact id plus account row, never email.** Account ids are deterministic
  and survive in-place re-authentication and slot-matching re-import. A
  side-by-side OAuth re-add of an existing slot gets a new id
  (`<base>__copyN`). Joining by email would hand one seat's dates to another
  seat in the same workspace. Ids are also reused: after a deletion the next
  seat in a workspace receives the lowest free `__copyN`, or the recomputed base
  id. So the key is the exact `accounts.id` plus an account incarnation, the
  SHA-256 digest of the row's `codex_installation_id`. That value is a uuid4
  created with each account row, backfilled for every legacy row, and kept by
  in-place credential replacement. The digest is stored, never the identifier.
- **Own table, not `dashboard_settings`.** `dashboard_settings.version` is the
  settings form's compare-and-set token. Storing per-account notes there would
  bump it on every lifecycle save and make an open settings form (timeouts
  included) fail with `settings_conflict`. A separate table leaves settings,
  their cache and their compare-and-set untouched.
- **Database, not a file in the data directory.** The database is where
  dashboard-edited state lives, it is shared by replicas, and it is backed up
  with everything else. A file would diverge between replicas.
- **No foreign key, no re-attachment.** Every other per-account table
  cascades on delete. Lifecycle notes are kept instead. Deleting an account
  leaves inert rows that no route returns. A later account row, even one given
  the same id, starts empty and saves under its own incarnation. Notes describe
  one subscription, and a re-added account is a new one. The retained rows stay
  in every export and can be restored exactly.
- **Precision is the shape of the value, not a column.** A value with a time is
  a `datetime`; a value without one is a `date`. Storing a separate precision
  column would let the two disagree.
- **Verbatim, not instants.** A civil date has no instant. Converting
  `2026-10-12` to midnight UTC would invent a cutoff the owner never stated.
  Local times keep their declared timezone (an IANA name or a fixed offset) and
  their declared precision (`HH:MM` or `HH:MM:SS`).
- **Revision compare-and-set per record.** Clearing keeps the row with every
  field unset and a higher revision, so a stale form can never succeed because
  the revision happened to come round again.
- **Account permissions.** Reading requires `accounts:read`, the permission
  that already gates the dashboard overview. Viewer and guest presets hold it;
  the own-scoped member preset does not. Writing requires `accounts:write`,
  like every other per-account control. Both routes are pinned in the route
  permission matrix.
- **Backup through the CLI.** An export written by the host process can be made
  owner-only (`0600`) and atomic. A browser download cannot. The commands use
  the configured database and work while the server is stopped, which is when a
  package rollback happens.

## Constraints

- No new setting, environment variable, navigation item or page.
- Lifecycle values are never read by account selection, sticky ownership,
  retries, service tiers, Fast mode or timeout budgets.
- IANA names are checked against the time zone database the server can see.
  Official images ship one. On a host without one, offsets still work and IANA
  names are rejected rather than guessed.

## Failure modes

- **Stale form.** The write is based on an old revision. The API returns 409
  `account_lifecycle_conflict` and changes nothing. The dashboard reloads the
  stored values so the operator can reapply the edit.
- **Account pending deletion or removed.** The lifecycle routes return 404. The
  row stays and is listed in exports with `accountPresent: false`. If the
  deletion is superseded in place, the row is still the same and the notes are
  visible again. If the account is finalized and a new row is created, even
  with the same id, the new row starts empty.
- **Out-of-range revision.** `expectedRevision` above `2147483646` (the int4
  column with room for the increment) is rejected with 422 before reaching the
  database. A restore that would push any revision past `2147483647` writes
  nothing and says so.
- **Restore against a changed store.** The snapshot token differs, the command
  exits non-zero and nothing is written. Take a fresh export and retry with its
  token.
- **Restore racing a dashboard write.** Every row write in a restore is
  conditional on the revision read in the same transaction, so a concurrent
  write makes the whole restore roll back.
- **Older build on a migrated database.** An older build refuses a database
  whose revision it does not know ("schema ahead") and fails startup on drift
  if the table is left behind. Use the rollback procedure below rather than
  stamping.

## Example

Sanitized from a real operator note. An account `acc_fixture_hello` whose plan
ends on 12 October 2026. The exact access cutoff is unknown, and the owner has
cancelled renewal:

```json
{
  "endsOn": {"precision": "date", "date": "2026-10-12", "time": null, "timezone": null},
  "renewsOn": null,
  "cancellationStatus": "cancelled",
  "expectedRevision": 0
}
```

The same account's weekly quota window resets at `2026-10-12T09:50:09Z`, and
one of its reset credits expires at `2026-10-22T20:26:37.869423Z`. Neither is a
lifecycle value: the dashboard keeps showing them in their own places. Entering
them as `Ends on` would wrongly state that access stops at 09:50 UTC on the
12th. Saving lifecycle metadata redeems nothing and does not change the
account's routing policy (for example an existing `burn_first`).

## Compatibility

- **Upgrade.** One additive revision,
  `20261005_000000_add_account_lifecycle_preferences`, on top of
  `20260912_000000_merge_thread_cache_and_bridge_retirement_heads`. No backfill.
  Existing accounts start with every field unset and revision `0`.
- **Restart, re-authentication, re-import.** Data lives in the database and is
  keyed by the id and account row those paths keep, so it survives all of them.
- **Later builds.** Any build whose migration graph contains this revision
  keeps the table. Carrying the change onto a newer upstream head:
  - Databases that never applied this revision take a fresh revision on the
    newer head. Its guarded create skips an existing table.
  - Databases that already applied it need an `OLD_TO_NEW_REVISION_MAP` entry
    (`app/db/alembic/revision_ids.py`) that maps this id to its parent,
    `20260912_000000_merge_thread_cache_and_bridge_retirement_heads`. Startup
    then replays the newer upstream revisions, and the fresh revision's guarded
    create leaves the existing rows alone.
  - Keeping this id and adding a merge revision instead means the merge must
    join this revision itself, not its parent, with the newer head. The
    topology check still flags the fork, because only merge revisions are
    exempt.
  - Never re-parent this same id: Alembic would treat the newer upstream
    revisions as already applied.
- **Restore revisions.** Every written record ends above both its stored
  revision and the revision recorded in the backup. A form opened before an
  export, a downgrade and an upgrade can therefore never match again.
- **Backup format.** `codex-lb/account-lifecycle-preferences`, `formatVersion`
  `1`. Restore rejects any other format or version rather than guess.

## Rollback

Source: revert the change's commit.

Data and schema, with the build that contains this change still installed:

1. Stop every codex-lb server process, on every replica. The export must be
   the last word: a save made after it would be lost, and a form left open
   could later match a restored revision.
2. `codex-lb account-lifecycle export --output <private path>`. Keep the file
   and the snapshot token it reports.
3. Downgrade the schema to the previous head. The database URL is read from
   the configured settings, so no credential is ever typed into the command
   line:

   ```sh
   python -c "from alembic import command; from app.core.config.settings import get_settings; \
   from app.db.migrate import _build_alembic_config; \
   command.downgrade(_build_alembic_config(get_settings().database_url), \
   '20260912_000000_merge_thread_cache_and_bridge_retirement_heads')"
   ```

   This drops only `account_lifecycle_preferences`.
4. Install and start the previous build.

To go forward again, start the build containing this change. The upgrade
recreates an empty table. Then run `codex-lb account-lifecycle export --output
<new path>` to read the empty snapshot token, followed by `codex-lb
account-lifecycle restore --input <private path> --expected-snapshot <that
token>`.

On SQLite installs, the automatic pre-migration database copy is another way
back.

## Out of scope, queued separately

The following each need their own change: per-account selection rank, "use
reset now", explicitly scheduled per-account resets with history, idempotency
and uncertain-send reconciliation, and any automatic redemption. This change
adds no control for them.
