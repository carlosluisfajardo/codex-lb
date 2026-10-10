## ADDED Requirements

### Requirement: The dashboard reads saved subscription dates through the existing read route

The dashboard SHALL read the saved lifecycle notes of each account in the
dashboard overview through `GET /api/accounts/{account_id}/lifecycle`, keyed by
the account's exact id, using the same client cache entry and the same 60-second
staleness as the account detail panel. One dashboard-level batch SHALL serve the
account grid, the account list and the attention summary. The dashboard MUST NOT
send any lifecycle write and MUST NOT poll the lifecycle route on its own.

The dashboard MUST NOT request lifecycle notes, and MUST NOT render cached ones,
until the dashboard session is initialized and holds `accounts:read` at any
scope. `accounts:write` SHALL NOT be required. A response whose `accountId`
differs from the requested account id SHALL NOT be rendered for that account.
Deleting an account, or creating one that may reuse a deleted id, SHALL discard
cached notes and reads in flight for that id exactly as the account detail panel
does, so a recycled id never shows a removed row's dates.

#### Scenario: A viewer without write access sees the dates

- **GIVEN** a session holding `accounts:read` but not `accounts:write`
- **WHEN** the dashboard shows an account whose `Renews on` is saved
- **THEN** the dashboard reads that account's lifecycle notes and shows the date

#### Scenario: A session without accounts:read neither requests nor renders dates

- **GIVEN** a session without `accounts:read`
- **AND** the client cache holds lifecycle notes for an account on the dashboard
- **WHEN** the dashboard renders its accounts
- **THEN** no lifecycle request is sent
- **AND** no lifecycle date is shown

#### Scenario: A response for another account is not shown

- **WHEN** the lifecycle read for `acc_a` returns notes whose `accountId` is `acc_b`
- **THEN** the dashboard shows the dates of `acc_a` as unavailable
- **AND** none of the returned dates are shown for `acc_a`

#### Scenario: A recycled id does not show the removed row's dates

- **GIVEN** the dashboard showed a saved `Ends on` for `acc_a`
- **WHEN** `acc_a` is deleted and a new account row given the id `acc_a` is imported
- **THEN** the dashboard never shows the removed row's `Ends on` for the new row

#### Scenario: Nothing is written

- **WHEN** the dashboard loads, refreshes and shows passed or due dates
- **THEN** no `PUT /api/accounts/{account_id}/lifecycle` request is sent

### Requirement: Account cards and list rows show both dates with their declared precision

Every dashboard account card and every account list row SHALL show `Ends on` and
`Renews on` for sessions that may read them. An unset date SHALL be shown as not
set. A civil date SHALL be shown as its full date, formatted with the shared
lifecycle date formatter and the date display preference, together with a
`Date only` marker. A `datetime` value SHALL be shown as its full date followed by
its time and timezone exactly as entered. Every set date SHALL also show the
number of calendar days until it: `in N days`, `today`, or that it passed `N` days
ago. While the notes are loading the dates SHALL be shown as loading, and when the
read failed they SHALL be shown as unavailable; neither state SHALL be shown as
not set. Long dates and timezones SHALL wrap inside their card or list cell.

#### Scenario: A date-only renewal three weeks away

- **GIVEN** the viewer's local date is `2026-10-10`
- **AND** an account's `Renews on` is the civil date `2026-10-31` and its `Ends on` is unset
- **WHEN** the dashboard shows that account in the grid or the list
- **THEN** `Renews on` shows `Oct 31, 2026`, a `Date only` marker and `in 21 days`
- **AND** `Ends on` shows not set

#### Scenario: A timed end keeps its time and timezone

- **WHEN** an account's `Ends on` is precision `datetime`, date `2026-11-03`, time `09:30` and timezone `America/New_York`
- **THEN** the dashboard shows the date `Nov 3, 2026` followed by `09:30 America/New_York`

#### Scenario: A failed read is not shown as not set

- **WHEN** the lifecycle read for an account fails
- **THEN** its dates are shown as unavailable rather than not set

### Requirement: Calendar-day distance uses civil days in the date's own calendar

The number of days until a date SHALL be the difference between the civil-day
ordinal of the saved date and the civil-day ordinal of the current date. For a
civil date the current date SHALL be the viewer's local calendar date, and the
saved date SHALL NOT be shifted by any timezone. For a `datetime` value the
current date SHALL be the calendar date in the value's recorded timezone, an IANA
zone name or a fixed UTC offset, and the time of day SHALL NOT change the count.
The distance MUST NOT be derived from elapsed milliseconds or 24-hour periods, so
daylight saving transitions, month and year ends and leap days never change it.
A saved value whose date is not a real calendar date, or whose timezone the
browser cannot evaluate, SHALL be shown as entered with its day count marked
unknown, and SHALL NOT be counted as due, passed or upcoming. The derived day
count SHALL follow the current date across a date boundary and when the page
becomes visible again, without sending any request.

#### Scenario: A daylight saving transition does not change the count

- **GIVEN** the viewer's timezone is `America/New_York` and the local date is `2026-03-07`
- **WHEN** a civil date `2026-03-09` is shown
- **THEN** its distance is `in 2 days`

#### Scenario: A leap day and a year end count whole days

- **WHEN** the current date is `2028-02-28` and the saved civil date is `2028-03-01`
- **THEN** the distance is `in 2 days`
- **WHEN** the current date is `2026-12-31` and the saved civil date is `2027-01-01`
- **THEN** the distance is `in 1 day`

#### Scenario: A timed value counts in its recorded timezone

- **GIVEN** the current instant is `2026-10-10T23:30:00Z`
- **WHEN** a `datetime` value dated `2026-10-11` is recorded in `Asia/Tokyo`
- **THEN** its distance is `today`, because it is already 11 October in Tokyo
- **WHEN** the same value is recorded in `-05:00`
- **THEN** its distance is `in 1 day`

#### Scenario: An unknown timezone is not classified

- **WHEN** a `datetime` value carries a timezone the browser does not know
- **THEN** the value is shown as entered and its day count is marked unknown
- **AND** the account is not counted as passed, due today or due within 7 days

### Requirement: Dates within seven days, today or passed are marked for attention

A set date whose distance is from 1 to 7 calendar days SHALL be marked due soon,
a distance of 0 SHALL be marked due today, and a negative distance SHALL be marked
passed. A passed `Renews on` SHALL be described as a recorded renewal date that
passed and needs confirmation, and MUST NOT be described as an expired
subscription or as lost access. A passed `Ends on` SHALL be described as a
recorded end date that passed. Unset dates and dates with an unknown day count
SHALL NOT be marked and MUST NOT imply that a subscription is active or expired.
Lifecycle attention MUST NOT change the account's status badge, quota remaining
percentages, rate-limit and quota-exceeded labels, quota reset labels,
reset-credit expiry or the routing of the account.

#### Scenario: A renewal date that passed needs confirmation

- **GIVEN** the viewer's local date is `2026-10-10`
- **WHEN** an account's `Renews on` is the civil date `2026-10-07`
- **THEN** the dashboard marks it passed and says it passed 3 days ago and needs confirmation
- **AND** the dashboard does not say the subscription expired

#### Scenario: The seventh day is due soon and the eighth is not

- **GIVEN** the viewer's local date is `2026-10-10`
- **WHEN** one date is `2026-10-17` and another is `2026-10-18`
- **THEN** `2026-10-17` is marked due soon
- **AND** `2026-10-18` is not marked

#### Scenario: Status and quota stay as reported

- **WHEN** an account with a passed `Ends on` is `active` with 64% of its weekly quota remaining
- **THEN** its status badge still shows active and its weekly quota still shows 64%

### Requirement: The accounts header summarizes accounts needing date attention

The dashboard `Accounts` header SHALL show a compact subscription-date summary
next to the existing registered, active and unavailable counts. The summary SHALL
count each account once, by its most urgent marked date: passed, then due today,
then due within 7 days. An account that is not counted that way SHALL be counted
as unknown when its notes could not be read or one of its set dates has an
unknown day count. When every read has settled and no account is counted, the
summary SHALL say that no date is due within 7 days. The summary SHALL be derived from the same batch the cards and
list show, MUST NOT change the registered, active or unavailable counts, and SHALL
NOT be shown to sessions without `accounts:read`.

#### Scenario: Each account is counted once by its most urgent date

- **GIVEN** account `acc_a` has a passed `Ends on` and a `Renews on` due in 3 days
- **AND** account `acc_b` has a `Renews on` due today
- **WHEN** the dashboard shows the summary
- **THEN** it shows 1 passed and 1 due today
- **AND** it shows no due-within-7-days count

#### Scenario: Nothing is due

- **WHEN** every account's notes were read and no date is within 7 days or passed
- **THEN** the summary says no date is due within 7 days
