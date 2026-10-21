## Context

The operator's F1-F5 reproductions use exported 1871f1e6/a9e5819, real bridge and selector code and controlled provider edges. The previous final-without-replay policy is explicitly superseded only for requests whose existing proof permits pre-dispatch account-neutral replay.

## Decisions

The outer streaming owner retains the existing full-history proof and projection. It disables owner waiting when that proof already permits immediate replay; selection still runs first, preserving healthy-owner preference and wrong-owner refusal. Expiry returns an ordinary required-owner refusal for an unattempted request, so the same proof remains authoritative.

Positive local contention bypasses the provider recovery window and returns its existing capacity error to the outer wait. Terminal/unknown provider classifications do not gain a capacity wait from that error's public shape.

The registry publishes a typed creation future carrying its request deadline and a separate shared result for recovery-ineligible joiners. The separate result follows healthy creation but refuses as soon as actual recovery begins. It never cancels or releases the creator. Both views use the established O(1) shared-future wait mechanism. A waiter deadline does not evict another owner's future. Creation receives the original published future explicitly, so a superseded creator cannot mark a replacement creator or its healthy waiters as recovering.

The first two owner rechecks retain their short cadence, then rechecks slow to eight seconds, clamped to the one original recovery deadline. This reduces selection/log/counter churn without changing the selection-input cache or persistence behavior (production caches inputs and writes only changed state).

## Example and constraints

A full-history continuation whose owner has 60 seconds of backoff immediately uses its existing verified replay on another account. An incomplete continuation instead keeps its owner/anchor during one bounded window. A stream-cap refusal waits on the original capacity budget. A healthy follow-up may share creation, but if that creation enters recovery the follow-up refuses while eligible waiters continue. Retained attempts, wrong-owner results, incomplete/mismatched history and ambiguous-send fences remain intact.

## M1 clarification

In M1, request B has independent positive full-history proof and no known or ambiguous same-operation effects. Sharing a creation key with incomplete creator A does not establish that they are the same logical operation. Distinguish the refusal emitted by A's actual owner-selection recovery from wrong-owner and other final refusals. Only B's initial creation catch may use that distinction with its existing proof and unattempted operation provenance; follow-up catches stay final. Known recovery attempts and existing operation/output provenance remain fenced. B uses the existing replay path, leaving A's future and deadline untouched. The four review lows are outside this correction.
