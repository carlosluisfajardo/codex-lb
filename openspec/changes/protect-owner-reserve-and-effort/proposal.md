## Why

The owner's stored credits must stay in reserve. The maintained fork also needs the retained always-ultra policy on models that advertise it.

## What Changes

- Exclude reserve accounts at 95% used in either standard window or when the long window is unknown, including quota bypasses.
- Exclude credit holders from additional-quota selection before standard credit evidence is replaced and fence retained sessions after a standard gate verdict.
- Raise ultra-capable models to the existing `max` upstream wire value without changing model or service tier.

## Impact

Source-only port of the retained LB21, LB21.1, and LB22 artifacts. No live installation, account changes, routing writes, or upstream upgrade.
