# Require applicable post-block evidence for Free-plan recovery

The maintained source treats a newer legacy synthetic primary row as recovery
evidence even when the Free plan has no primary capacity. With an available long
window sampled before the block, the marking replica clears its future429 deadline
and makes the account active. The hermetic discriminator observed this exact
ACTIVE-versus-RATE_LIMITED failure.

Exclude non-applicable primary rows from the existing freshness selector for
zero-primary-capacity plans. Keep newest runtime/persisted block ordering, reset
history, long-window exhaustion, account ownership and credit policy unchanged.
Do not adopt upstream #2048's broader refactor or credit changes, or repeat #2078.
