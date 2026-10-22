# Preserve bounded bridge capacity recovery and creator cancellation

## Why

LB14 preserves owner recovery guards but three accepted source findings remain: a confirmed rejected-anchor reattach fails immediately at stream capacity, a proof-bearing cooldown/backoff owner at capacity cannot reach existing guarded replay, and cancellation during planned registry eviction can orphan the creator reservation.

## What Changes

- Preserve positive selection provenance across public capacity errors so an already-authorized rejected-anchor reattach can use the existing bounded capacity plan.
- Keep immediate replay available for unattempted, independently proven full-history continuations when cooldown/backoff coexists with capacity.
- Settle the exact creator reservation when planned close or the subsequent capacity recheck is cancelled, including level and repeated cancellation.

## Impact

HTTP bridge selection, request recovery and registry finalization only. Public error envelopes, file/account ownership, full-history proof requirements, original request budgets, API reservation settlement and ambiguous-send fences remain binding. No runtime configuration or installation changes.
