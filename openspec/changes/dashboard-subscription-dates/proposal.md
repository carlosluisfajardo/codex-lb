# Show saved subscription dates on the dashboard account cards and list

## Why

Operators already record, per account, when its subscription ends or renews
(`account-lifecycle-metadata`, `GET /api/accounts/{account_id}/lifecycle`). The
dates are shown only inside the account detail panel of the accounts page, so an
operator who watches the dashboard sees quota, status and reset credits but not
that a recorded end or renewal date is close or has already passed. Two accounts
reached their recorded dates before anyone noticed.

The dates are operator notes, not provider facts. Showing them must not turn a
passed renewal date into a claim that a subscription expired, must not mix them
with quota resets or reset-credit expiry, and must not invent a day count for a
value the browser cannot evaluate.

## What Changes

- Read the saved lifecycle notes of every account on the dashboard overview with
  the existing read route, the existing per-account query key and its existing
  staleness, in one dashboard-level batch shared by the account grid, the account
  list and the attention summary. Only sessions holding `accounts:read` read or
  see them; nothing is written.
- Show `Ends on` and `Renews on` on every account card and as one new column of
  the account list: the full date, `Date only` or the time and timezone exactly as
  entered, and the number of calendar days until the date.
- Mark a date due within 7 calendar days, due today, or passed. A passed renewal
  date reads as a recorded date that passed and needs confirmation.
- Add a compact attention summary next to the existing account availability
  summary: accounts with a passed date, a date due today, a date due within 7
  days, and accounts whose dates could not be read or evaluated.

## Non Goals

- No backend, API, schema, storage or permission change. No new endpoint, no
  batch endpoint, no new setting, navigation item or persisted preference.
- No inference or advance of any date, cancellation status, provider access or
  billing state. A passed renewal date is never rolled forward.
- No change to account status, quota remaining percentages, rate-limit labels,
  quota reset labels, reset-credit expiry or routing.
- No sorting by date in the account list (it would add a persisted sort key).

## Capabilities

### New Capabilities

- `dashboard-subscription-dates`: dashboard presentation of the saved
  subscription end and renewal dates, their calendar-day distance, attention
  states and attention summary.

### Modified Capabilities

None. `account-lifecycle-metadata` keeps its API, storage, permissions and
account detail panel; this change only reads through its existing route.

## Impact

- Frontend only: `frontend/src/features/dashboard/` (one new pure module, one new
  hook, the account card, card grid, list, summary line and page components),
  `en`, `ko` and `zh-CN` strings, unit, integration and browser smoke tests.
- Requests: one existing `GET /api/accounts/{account_id}/lifecycle` per overview
  account when the dashboard loads and after the existing 60-second staleness,
  shared with the accounts page cache. No polling of its own.
- Compatibility and rollback: see `context.md`.
