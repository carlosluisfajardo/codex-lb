# Context

Normative requirements live in
`specs/dashboard-subscription-dates/spec.md`. This file records why the change is
shaped this way, how it behaves when something fails, and how to back it out.

## Purpose and scope

The saved `Ends on` and `Renews on` notes of each account
(`account-lifecycle-metadata`) become visible where operators watch their pool:
the dashboard account cards, the account list and the accounts header. The
dashboard only reads them. Editing stays in the account detail panel.

## Decisions and the alternatives rejected

- **Read the existing route per account, no batch endpoint.** A batch or
  overview field would change the API, schema and backend for a frontend need.
  One `GET /api/accounts/{account_id}/lifecycle` per overview account, cached
  under the existing `["accounts", "lifecycle", accountId]` key with the existing
  60-second staleness, is enough for a pool of accounts. The cache is shared with
  the account detail panel, so a save there shows on the dashboard at once, and
  the existing delete and import handlers already discard notes for a removed or
  recycled id.
- **One batch, one map.** A single `useQueries` call in the dashboard page builds
  one map from account id to read state. Cards, list rows and the summary read
  that map, so the three views never disagree and switching the view mode sends
  no request.
- **`accounts:read`, once initialized.** The route requires `accounts:read`; the
  dashboard asks for nothing before the session says it holds that permission.
  Gating on `accounts:write` would hide the dates from viewers who may read them.
- **Seven calendar days is "due soon".** A week gives an operator a weekend and
  a working week to act on a renewal or an end date, and stays short enough that
  the warning is not permanent noise on monthly plans. Today is day 0. Negative
  days are passed.
- **Civil days, never elapsed time.** The distance is the difference of two
  civil-day ordinals computed with integer calendar arithmetic. Elapsed
  milliseconds divided by 24 hours lose a day across a daylight saving change
  and depend on the time of day.
- **Whose "today".** A civil date has no timezone, so it is compared with the
  viewer's local calendar date and never converted to an instant. A `datetime`
  value declares its timezone, so it is compared with the calendar date in that
  zone: an IANA name through `Intl.DateTimeFormat`, a fixed offset by shifting
  the current instant. The time of day is shown but does not change the count.
- **Unknown stays unknown.** The response schema checks only the shape of a
  date. A date that is not a real calendar date, or a zone the browser's time
  zone data does not contain, is shown as entered with "day count unknown" and
  is never counted as due or passed.
- **Wording of a passed date.** The dates are notes typed by an operator. A
  renewal date that passed most likely renewed, or did not; only the provider
  knows. The dashboard therefore says the recorded date passed and needs
  confirmation, and never "expired".
- **Placement.** Cards show the two dates as a full-width block under the
  account identity, before the quota bars. The list adds one column next to
  Plan. The summary sits after the existing availability counts and wraps on
  narrow screens. Quota, status, reset and reset-credit presentation is
  unchanged.
- **No date sort.** Sorting the list by date would add a value to the persisted
  list-sort preference. It is left out.
- **Day boundaries.** A local check runs when notes arrive, once a minute and
  when the window regains focus or visibility. It re-renders only when a
  displayed day count would change, and it sends no request.

## Constraints

- No backend, API, schema, settings, navigation or persisted preference change.
- No write: the dashboard never saves, advances or clears a date.
- Lifecycle state never feeds the status badge, the availability counts, quota
  figures or routing.

## Failure modes

- **Read fails or returns another account's id.** The account's dates show as
  unavailable, even when older notes are still cached, and the summary counts
  the account as unknown. Nothing is shown as not set.
- **Permission absent or the session is still initializing.** No request is
  sent and no cached note is rendered.
- **Account deleted or its id recycled.** The existing delete and import
  handlers remove or reset the cached notes and cancel reads in flight, so the
  removed row's dates never show for the new row.
- **Stale notes.** A save made in this browser updates the shared cache at once.
  A save made elsewhere appears after the 60-second staleness at the next mount
  or reset. The dashboard refresh button keeps refreshing only the dashboard
  queries, as before.
- **Browser without the recorded IANA zone.** The value is shown as entered and
  its day count is unknown.
- **Many accounts.** One read per account on first load. Reads are deduplicated
  by the shared cache and are not repeated while fresh.

## Example

An account whose renewal was recorded as the civil date `2026-10-31` and whose
end date is unset, viewed on 10 October 2026:

```json
{
  "accountId": "acc_fixture_renewal",
  "endsOn": null,
  "renewsOn": {"precision": "date", "date": "2026-10-31", "time": null, "timezone": null},
  "cancellationStatus": null
}
```

The card shows `Ends on: Not set` and `Renews on: Oct 31, 2026 · Date only · in
21 days`. On 24 October the renewal shows `in 7 days` and is marked due soon, on
31 October `today`, and from 1 November `passed 1 day ago · needs confirmation`
until the operator saves a new date. The summary counts the account once. Its
weekly quota percentage, status badge and reset-credit expiry are unchanged.

## Compatibility and rollback

- **Compatibility.** Frontend only. Going back to a build without this change
  loses nothing: the notes stay in the database and in the account detail
  panel. The change needs a backend that serves the lifecycle route
  (`account-lifecycle-metadata`); on a backend without it every read fails and
  the dates show as unavailable.
- **Rollback.** Revert the change's commit and rebuild the frontend. There is
  no data, schema or setting to undo.
