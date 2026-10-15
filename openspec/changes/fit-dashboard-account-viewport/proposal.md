## Why

Saved subscription dates make dashboard account cards taller than the fixed
11.5rem row assumption. At desktop widths the scroll viewport can clip the first
row's actions and conceal the next row, while hidden scrollbars offer no cue.

## What Changes

- Size the card viewport from two rendered grid rows, including their gap, and
  remeasure when account content or the responsive grid changes.
- Keep native scrolling discoverable and keyboard accessible in cards and list
  mode. Give the list one scroll container for both axes, within its current cap.
- Pin tall-card actions, subsequent-row visibility and keyboard access to later
  accounts at 1440px; retain narrow-screen containment.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Replace the fixed account-card row height with a
  content-aware two-row viewport and accessible account scrolling.

## Impact

Only dashboard account viewport components and focused frontend tests change.
Account content, actions, ordering, API reads and stored preferences are unchanged.
