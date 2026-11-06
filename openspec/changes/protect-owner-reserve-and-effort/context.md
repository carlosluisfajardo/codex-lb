This source-only port adapts the retained LB21/LB21.1/LB22 source diffs and tests from `/Users/Shared/rebuild-draft-20261104/lb/evidence/`. The 04:10Z and 08:00Z sections of `LB-RESTORE.md` explain the additional-quota bypass, retained-session fence, 6% guard hold, and `ultra` to `max` policy.

For example, an account holding credits at 95% weekly usage is excluded even if the caller bypasses ordinary quota status. At 10% usage it may serve standard traffic, but never additional-quota traffic.

The session fence learns reserve state during standard builds; it does not itself refresh upstream usage. The separate reserve guard pauses at 6% remaining to fence idle retained sessions before the 95% selector margin. In-flight requests and stale upstream readings remain limits of this design.

The fork at `b8512db1` differs from the retained installed runtime: it does not contain the Luna-to-Sol rewrite or the reserve guard's forced-usage-refresh/expected-state API contract. This port preserves the fork's existing model handling and does not imply runtime parity, installation, or a checked swap. The operator owns a future replacement and compatibility assessment.
