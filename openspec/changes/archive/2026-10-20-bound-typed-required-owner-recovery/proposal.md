# Bound typed required-owner recovery

## Why

First HTTP bridge creation currently collapses temporary required-owner cooldown and terminal eligibility failures into the same refusal. A brief owner outage cannot safely earn a wait until its reason survives selection and hard-affinity remapping.

## What Changes

- Carry a request-local typed failure reason from core selection through hard affinity and service selection, with unknown as the compatible constructor default.
- Preserve terminal or unknown eligibility failures ahead of local contention, including mixed blockers.
- Permit first bridge creation to wait for the same required owner only on positively typed cooldown, error backoff, or local contention, before any response-create dispatch.
- Bound the entire recovery window by the original request deadline and 30 seconds, without resetting on reselection or opening an alternate owner.

## Impact

Affected capabilities: `sticky-session-operations`, `responses-api-compat`. No persisted fields, settings, quota policy, health classifier, or public error envelope changes. Source qualification targets the explicitly admitted deployed-source maintenance base.
