## ADDED Requirements

### Requirement: Lifecycle metadata belongs to one account row

The system SHALL store lifecycle metadata under the account's exact
`accounts.id` together with an account incarnation, the SHA-256 digest of that
account row's `codex_installation_id`. The system MUST NOT read, copy, merge or
infer lifecycle metadata through an account's email address, ChatGPT account
id, workspace, seat or any attribute other than the exact id and its
incarnation. An account row that has never had metadata saved SHALL report
every lifecycle field as unset at revision `0`, even when another account
shares its email address. Re-authenticating or re-importing an account in place
keeps its row, its id and its incarnation, and therefore keeps its metadata.

#### Scenario: A new account id does not inherit metadata through a shared email

- **GIVEN** account `acc_a` has `Ends on` set to the civil date `2026-10-12`
- **AND** account `acc_b` is added with the same email address but a different id
- **WHEN** the dashboard reads the lifecycle metadata of `acc_b`
- **THEN** every lifecycle field of `acc_b` is unset and its revision is `0`
- **AND** the metadata of `acc_a` is unchanged

#### Scenario: Replacing an account's credentials in place keeps its metadata

- **GIVEN** account `acc_a` has saved lifecycle metadata
- **WHEN** the credentials of `acc_a` are replaced in place, keeping its row
- **THEN** reading the lifecycle metadata of `acc_a` returns the same fields and revision

### Requirement: Lifecycle dates preserve their declared precision

`Ends on` and `Renews on` SHALL each be either unset, a civil date with
precision `date`, or a local date and time with precision `datetime` and an
explicitly supplied timezone. A civil date MUST be written `YYYY-MM-DD` and MUST
be a real calendar date. A time MUST be written `HH:MM` or `HH:MM:SS` on a
24-hour clock. A timezone MUST be either a name present in the IANA time zone
database available to the server or a fixed UTC offset written `+HH:MM` or
`-HH:MM` between `-12:00` and `+14:00`. Every digit MUST be an ASCII digit.
A `date` value MUST NOT carry a time or a timezone, and a `datetime` value MUST
carry both. The system SHALL store and
return the date, time and timezone exactly as accepted and MUST NOT convert a
value to an instant, add a time to a civil date, or substitute a timezone. A
request that violates any of these rules SHALL be rejected with HTTP 422 and
SHALL change nothing. Lifecycle dates are independent of quota window resets,
reset-credit expiry and login times, and the system MUST NOT derive them from
those values.

#### Scenario: A date-only end is returned without a time or timezone

- **WHEN** an operator saves `Ends on` as `{precision: "date", date: "2026-10-12"}`
- **THEN** reading the account returns `Ends on` with precision `date`, date `2026-10-12` and no time or timezone

#### Scenario: A local time keeps its declared timezone and precision

- **WHEN** an operator saves `Renews on` as precision `datetime`, date `2026-11-03`, time `09:30` and timezone `America/New_York`
- **THEN** reading the account returns exactly that date, the time `09:30` and the timezone `America/New_York`

#### Scenario: An impossible calendar date is rejected

- **WHEN** an operator saves `Ends on` with date `2026-02-30`
- **THEN** the request is rejected with HTTP 422
- **AND** the stored lifecycle metadata and its revision are unchanged

#### Scenario: Non-ASCII digits are rejected

- **WHEN** an operator saves a `datetime` value whose time or offset uses non-ASCII digits
- **THEN** the request is rejected with HTTP 422

#### Scenario: A time without a timezone is rejected

- **WHEN** an operator saves a `datetime` value with a time but no timezone
- **THEN** the request is rejected with HTTP 422

#### Scenario: A date-only value carrying a time is rejected

- **WHEN** an operator saves a `date` value that also carries a time
- **THEN** the request is rejected with HTTP 422

### Requirement: Cancellation status is manual

The cancellation status SHALL be unset, `not_cancelled` or `cancelled`, and
SHALL change only when an operator saves it. Saving a cancellation status MUST
NOT set, clear or modify `Ends on` or `Renews on`.

#### Scenario: Marking an account cancelled leaves its dates as entered

- **GIVEN** account `acc_a` has `Renews on` set and `Ends on` unset
- **WHEN** an operator saves the cancellation status `cancelled` and resubmits the same dates
- **THEN** `Renews on` is unchanged and `Ends on` stays unset

### Requirement: Lifecycle routes are gated by account permissions

`GET /api/accounts/{account_id}/lifecycle` SHALL require a dashboard session
holding `accounts:read`, and `PUT /api/accounts/{account_id}/lifecycle` SHALL
require `accounts:write`. A principal without the required permission SHALL
receive HTTP 403 and the stored metadata SHALL be unchanged.

#### Scenario: A viewer reads but cannot write

- **GIVEN** a principal whose role grants `accounts:read` but not `accounts:write`
- **WHEN** they read an account's lifecycle metadata and then try to save it
- **THEN** the read succeeds and the write is rejected with HTTP 403

### Requirement: Lifecycle writes are compare-and-set on a revision

Reading an account's lifecycle metadata SHALL return a revision, which is `0`
when nothing was ever saved for that account row. A write SHALL replace all three fields
at once and MUST carry the revision it was based on, an integer from `0` to
`2147483646`; a value outside that range SHALL be rejected with HTTP 422. When
that revision differs from the stored revision the system SHALL reject the write with HTTP 409 and
error code `account_lifecycle_conflict` and SHALL change nothing. Every accepted
write SHALL increase the revision by one. Clearing every field SHALL keep the
record with all fields unset and an increased revision, so a revision is never
reused for the same record.

#### Scenario: Save, clear and reload

- **GIVEN** account `acc_a` has no saved metadata and revision `0`
- **WHEN** an operator saves `Ends on` with expected revision `0`
- **THEN** the response carries revision `1`
- **WHEN** the operator then saves every field unset with expected revision `1`
- **THEN** the response carries revision `2` and every field unset
- **AND** a later read from a separate process returns the same fields and revision `2`

#### Scenario: Two writers based on the same revision

- **GIVEN** account `acc_a` has revision `3`
- **WHEN** two writes both based on revision `3` are submitted
- **THEN** exactly one is accepted and returns revision `4`
- **AND** the other is rejected with HTTP 409 `account_lifecycle_conflict`

### Requirement: Lifecycle metadata outlives account removal as an inert record

Lifecycle metadata SHALL be stored without a foreign key to `accounts`.
Deleting an account MUST NOT delete its lifecycle metadata. While an account id
is absent or pending deletion, its lifecycle routes SHALL respond with HTTP 404
`account_not_found`. Retained metadata of a removed account row MUST NOT be shown
for any other account row, including a later row that is given the same id: that
row SHALL start with every field unset at revision `0`, and saving its metadata
SHALL NOT modify the retained record. When a pending deletion is superseded by
an in-place credential replacement, the account keeps its row and its metadata.

#### Scenario: A re-created id starts empty and the retained record is kept

- **GIVEN** account `acc_a` has saved lifecycle metadata at revision `1`
- **WHEN** `acc_a` is deleted and its deletion is finalized
- **THEN** reading `/api/accounts/acc_a/lifecycle` returns HTTP 404
- **WHEN** a new account row with the id `acc_a` is added
- **THEN** reading its lifecycle metadata returns every field unset at revision `0`
- **WHEN** the operator saves metadata for the new row
- **THEN** the retained record of the removed row is unchanged and still exported

#### Scenario: Superseding a pending deletion keeps the metadata

- **GIVEN** account `acc_a` has saved lifecycle metadata and its deletion is pending
- **WHEN** its credentials are replaced in place before the deletion is finalized
- **THEN** reading its lifecycle metadata returns the saved fields again

### Requirement: Lifecycle metadata does not change routing

Lifecycle metadata SHALL be informational. Account selection, eligibility,
routing policy, sticky ownership and in-flight requests MUST NOT read or change
because lifecycle metadata was saved, cleared or restored.

#### Scenario: Saving lifecycle metadata leaves the routing policy unchanged

- **GIVEN** account `acc_a` has routing policy `burn_first`
- **WHEN** an operator saves lifecycle metadata for `acc_a`
- **THEN** the account list still reports routing policy `burn_first` for `acc_a`

### Requirement: Private backup and compare-and-set restore

`codex-lb account-lifecycle export --output <path>` SHALL write a JSON document
containing only lifecycle fields keyed by account id, with no credential, token
or email. The document SHALL carry the format name `codex-lb/account-lifecycle-
preferences`, `formatVersion` `1`, a snapshot token derived from every stored
id, incarnation and revision, and one entry per stored record with its
`accountIncarnation`, including retained records whose account row is absent,
flagged by `accountPresent`. The file SHALL be created with owner-only
permissions and become visible at its final path only when complete, and an
existing file at that path MUST NOT be replaced. `codex-lb account-lifecycle
restore --input <path> --expected-snapshot <token>` SHALL apply the document
only when the token equals the current snapshot, and SHALL then make the stored
fields equal to the document in one transaction: records in the document receive
its fields, stored records absent from the document are cleared, and every
written record's revision becomes greater than both its stored revision and the
revision the document records for it. A restore that would need a revision above
`2147483647` SHALL change nothing. A mismatched token, an unknown format or
format version, or an invalid field SHALL change nothing.

#### Scenario: Export creates a private file and never overwrites

- **WHEN** an operator exports to a path that does not exist
- **THEN** the file is created with mode `0600` and lists every stored record, including absent accounts
- **WHEN** the operator exports to the same path again
- **THEN** the command fails and the existing file is unchanged

#### Scenario: Restore round-trips a backup

- **GIVEN** an export taken at snapshot `S`
- **AND** the stored metadata was changed afterwards
- **WHEN** an operator restores the export with the current snapshot token
- **THEN** every record's fields equal the exported fields
- **AND** no record's revision decreased

#### Scenario: Restore after a schema rollback never reuses a revision

- **GIVEN** an export taken while `acc_a` was at revision `3`
- **AND** the lifecycle table was dropped and recreated empty by a downgrade and upgrade
- **WHEN** the operator restores the export
- **THEN** `acc_a` comes back at a revision greater than `3`
- **AND** a write based on revision `1` is rejected with HTTP 409

#### Scenario: Restore with a stale snapshot changes nothing

- **WHEN** an operator restores with a snapshot token that no longer matches
- **THEN** the command fails and every stored record is unchanged

### Requirement: The lifecycle schema change is additive and reversible

The system SHALL add lifecycle storage with one Alembic revision that creates
only the `account_lifecycle_preferences` table and alters no existing table or
row. Downgrading that revision SHALL drop only that table. The migration graph
SHALL keep a single head.

#### Scenario: Upgrade, downgrade and upgrade again

- **GIVEN** a database at the previous head that holds accounts
- **WHEN** it is upgraded to head, downgraded to the previous head and upgraded again
- **THEN** the accounts are unchanged at every step
- **AND** the lifecycle table exists exactly when the database is at head

### Requirement: Account detail shows lifecycle metadata beside the routing policy

The accounts page SHALL show the lifecycle fields of the selected account inside
the existing account detail panel and MUST NOT add a navigation item or page.
Each date SHALL be shown with its declared precision: a civil date as a date
labelled date only, and a `datetime` value as its date, its time as entered and
its timezone. Unset fields SHALL be shown as not set. The section SHALL show the
account's current routing policy read-only, together with a statement that the
routing policy orders eligible accounts for new requests, does not override
pause, quota, health or cooldown limits, and when changed does not move requests
already in progress. Operators with write
access SHALL be able to edit the fields and Save or Cancel; Cancel SHALL discard
the draft without a request, and a rejected save SHALL show its error. Operators
without write access SHALL see the values and no edit control. The section MUST
NOT offer reset, redemption, scheduling or ranking controls.

#### Scenario: Read-only presentation of declared precision

- **GIVEN** an account whose `Ends on` is the civil date `2026-10-12` and whose `Renews on` is unset
- **WHEN** the operator opens its detail panel
- **THEN** `Ends on` shows `2026-10-12` marked date only and `Renews on` shows not set
- **AND** the current routing policy is shown next to the lifecycle fields

#### Scenario: Cancel discards the draft

- **WHEN** an operator edits a lifecycle field and chooses Cancel
- **THEN** no write request is sent and the saved values are shown again

#### Scenario: Save sends the full replacement with the read revision

- **GIVEN** the panel shows revision `2`
- **WHEN** an operator changes `Ends on` and chooses Save
- **THEN** a write is sent with all three fields and expected revision `2`

#### Scenario: Viewers cannot edit

- **GIVEN** an operator without account write access
- **WHEN** they open an account's detail panel
- **THEN** the lifecycle values are shown and no edit control is offered
