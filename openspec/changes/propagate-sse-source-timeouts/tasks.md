## Reproduction

- [x] Observe finite source-timeout and ASGI regressions fail on the preimage.

## Correction and verification

- [x] Distinguish source failures from the keepalive deadline.
- [x] Verify idle/source-settlement races, genuine idle and cancellation.
- [x] Mutation-walk protective cases and restore the final source exactly.
- [x] Synchronize the canonical requirement and validate available checks.
- [ ] Obtain the original Sol source challenge and required risky review.
- [ ] Publish the source PR with actual checks and remaining limitations.

Local evidence: four intended preimage failures; 77 focused tests passed after
the fix and exact mutation restoration; four settled-source mutation failures
and one cancellation mutation failure; Ruff, formatting, focused types, timing
and cancellation checks passed. One existing Starlette/AnyIO deprecation warning
was reported. Strict OpenSpec CLI validation is unrun locally because no
already-available CLI was found; dependencies were not acquired. Hosted CI,
merge and installation are separate from these local source checks.
