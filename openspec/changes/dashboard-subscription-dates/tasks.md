## 1. Specification

- [x] 1.1 Add the `dashboard-subscription-dates` capability with requirements for
  the shared read, declared precision, civil-day distance, attention states and
  the attention summary.
- [x] 1.2 Record the 7-day threshold, placement, failure modes, an example and
  rollback in `context.md`.

## 2. Data

- [x] 2.1 Add a pure dashboard lifecycle module: civil-day ordinals, the current
  date in the viewer's zone, an IANA zone or a fixed offset, day distance,
  attention classification and the per-account summary.
- [x] 2.2 Add one dashboard-level `useQueries` batch over the existing lifecycle
  read and cache key, gated on an initialized session with `accounts:read`,
  rejecting a mismatched `accountId`, plus a local day clock that re-renders
  only when a displayed day count changes.

## 3. Dashboard

- [x] 3.1 Show `Ends on` and `Renews on` with precision, time and timezone,
  day distance and attention on the account cards.
- [x] 3.2 Add the subscription dates column to the account list.
- [x] 3.3 Add the attention summary to the accounts header.
- [x] 3.4 Add `en`, `ko` and `zh-CN` copy.

## 4. Regression coverage

- [x] 4.1 Dashboard integration test on the real page path, seen failing before
  the change.
- [x] 4.2 Date arithmetic tests across daylight saving, month, year and leap-day
  boundaries, IANA and fixed-offset zones, the 7-day edge and unknown values.
- [x] 4.3 Hook tests: permission and initialization gate, `accounts:write` not
  required, mismatched id, failure distinct from unset, recycled id after delete
  and import.
- [x] 4.4 Component tests for cards, list and summary, including unchanged status
  and quota.
- [x] 4.5 Browser smoke with lifecycle fixtures for grid and list at 320, 390
  and 1440 pixels, including long timezone containment.

## 5. Validation

- [x] 5.1 Frontend lint, build (typecheck), fast tests and the browser smoke.
- [x] 5.2 `openspec validate dashboard-subscription-dates --strict` and
  `openspec validate --specs`.
- [x] 5.3 Package build with wheel asset verification.
