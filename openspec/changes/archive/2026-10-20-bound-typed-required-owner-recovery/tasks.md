## 1. Typed selection

- [x] 1.1 Add compatibility-defaulted failure reasons and prove core terminal/temporary/unknown and mixed-blocker precedence.
- [x] 1.2 Preserve reasons and reset metadata through hard affinity, local admission, AccountSelection and service selection.

## 2. Bounded bridge recovery

- [x] 2.1 Wait only for a positively typed temporary failure of the same required owner before dispatch, within one original-budget/30-second window.
- [x] 2.2 Preserve public refusals, attempt-count fences, lease cleanup and singleflight cancellation semantics.

## 3. Qualification

- [x] 3.1 Exercise the actual selector-to-bridge path for recovery, terminal/mixed refusal, expiry, cancellation and ownership fences.
- [x] 3.2 Witness right-reason red/green and production-only mutation walks for protective negatives; restore exact production bytes.
- [x] 3.3 Run focused regressions, lint and type checks.
- [x] 3.4 Sync the qualified requirements into the main capability specs.
- [x] 3.5 Setup runs strict OpenSpec validation and required aggregate checks on the final bytes before publication.
