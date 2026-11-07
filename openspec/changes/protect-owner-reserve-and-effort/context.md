This source-only port adapts the retained LB21/LB21.1/LB22 source diffs and tests from `/Users/Shared/rebuild-draft-20261104/lb/evidence/`. The 04:10Z and 08:00Z sections of `LB-RESTORE.md` explain the additional-quota bypass, retained-session fence, 6% guard hold, and `ultra` to `max` policy.

For example, an account holding credits at 95% weekly usage is excluded even if the caller bypasses ordinary quota status. At 10% usage it may serve standard traffic, but never additional-quota traffic.

Quota-derived status and reserve eligibility are separate: credit metadata can leave an exhausted weekly account `active` while the reserve gate excludes it. The [usage-refresh-policy contract](../../specs/usage-refresh-policy/spec.md) describes that status distinction; [account-routing](../../specs/account-routing/spec.md) owns the reserve decision.

Reasoning allowlists authorize the client's effort before the catalog floor runs. For a catalog model advertising `ultra`, even an omitted effort or an accepted lower key-enforced effort is raised and serialized as wire `max`. Other models and missing catalog snapshots keep existing defaults and enforcement. The [Responses contract](../../specs/responses-api-compat/spec.md) defines this precedence, which the API-key guide also explains.

The session fence learns reserve state during standard builds; it does not itself refresh upstream usage. The separate reserve guard pauses at 6% remaining to fence idle retained sessions before the 95% selector margin. In-flight requests and stale upstream readings remain limits of this design.

The fork at `b8512db1` differs from the retained installed runtime: it does not contain the Luna-to-Sol rewrite or the reserve guard's forced-usage-refresh/expected-state API contract. This port preserves the fork's existing model handling and does not imply runtime parity, installation, or a checked swap. The operator owns a future replacement and compatibility assessment.

## CI qualification

The API-key integration fixtures use a real authoritative model registry with an explicitly unadvertised service tier. This supplies the complete snapshot contract used by the effort floor while preserving the accounting, forwarding, and source-route tier assertions.

Compact success and quota-header fixtures give credit-bearing accounts known, eligible standard usage in both windows. Separate HTTP cases refuse unknown or 95%-used long windows without calling upstream, then recover after safe standard usage arrives. Secondary quota headers remain visible normally and hidden for API-key clients when configured.

Replica tests distinguish quota bookkeeping from reserve eligibility: a fresh unlimited-credit or positive-balance sample may clear a quota block while an exhausted standard window still prevents selection. An affirmative credit flag alone does not clear the quota block. Same-second stale observations remain insufficient, and later safe standard usage restores selection on both replicas after the existing cooldown.

The original CI scanner reported six fixed Python findings in aiohttp, brotli, certifi, and dnspython. The dependency floors now exclude the reported vulnerable direct versions, and the lock selects aiohttp 3.14.4, brotli 1.2.1, certifi 2026.10.18, and transitive dnspython 2.9.0. Aiohttp 3.14.4 also requires yarl 1.25.1. Other package records remain unchanged apart from project dependency metadata; hosted image scanning remains separate evidence.

The hosted Rust audit reported eleven findings in the native egress dependency graph. Hyper 1.9.0 removes the unmaintained `pin-utils` dependency. The exact workspace pins select rustls 0.23.46 and Tokio 1.51.6, with the required Tokio macros 2.7.0 companion; the lock also selects rustls-webpki 0.103.16, zstd 0.13.4, zstd-safe 7.4.0, and zstd-sys 2.1.2. The eight updated package records and removed `pin-utils` record are the only Rust lock changes. Existing TLS and compression features, WebSocket fork revisions, toolchain, and advisory, license, and source policies remain unchanged. For example, requests advertising zstd support retain the native client's zstd decoder rather than avoiding the affected dependency by disabling compression.

Contributor metadata follows the existing GitHub identity 252236154 from `luawl` to `TheFoolThatDoesntBelongToThisEra`, preserving its display name and historical code/test credit without adding another person or attributing this port to them.
