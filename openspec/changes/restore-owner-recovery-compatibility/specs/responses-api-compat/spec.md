## MODIFIED Requirements

### Requirement: Bounded pre-dispatch required-owner recovery

First HTTP bridge creation MAY wait only when selection positively identifies cooldown or error backoff for the same required owner and the existing guarded account-neutral durable full-history replay proof is unavailable. When that proof permits replay and no upstream response-create attempt occurred, an owner-unavailable refusal SHALL use the existing immediate replay path without first waiting for owner recovery. Positive local contention before any upstream dispatch SHALL retain the existing outer request-deadline capacity wait, including follow-up creation and capacity keepalives. The provider-owner recovery window SHALL expire at the earlier of the original request deadline and 30 seconds after the first temporary failure. Reselection SHALL NOT renew that window, SHALL itself be bounded by its deadline, and SHALL NOT accept a success that returns after expiry. Outer capacity handling and follow-up replacement SHALL NOT start another recovery window. A mismatched selected owner or a retained response-create attempt SHALL remain final without replay or anchor removal. At recovery expiry, the existing independent guarded replay path SHALL remain available if its proof permits it; expiry SHALL NOT start a second owner-recovery window. Ordinary no-account refusals SHALL preserve the existing guarded verified-full-resend and request-path terminal-owner retirement policies; the absence of temporary provenance SHALL NOT itself revoke those independent proofs. Replay-rejection attribution SHALL remain available even when a final refusal forbids replay. Every reselection SHALL retain the required owner, forbid fallback and preserve the previous-response anchor. A retained response-create attempt count greater than zero SHALL forbid this recovery even when the sent timestamp is absent. Unknown and terminal-for-recovery failures SHALL leave required-owner selection promptly without a capacity wait; any existing guarded recovery SHALL remain subject to its independent proof requirements. Recovery SHALL use the existing scheduler and lifecycle ownership, release failed-selection leases before sleeping, and settle singleflight creation and lease ownership on cancellation without dispatching upstream.

#### Scenario: Temporary owner recovers
- **GIVEN** the required owner is temporarily in cooldown before any upstream dispatch
- **WHEN** it becomes selectable within the recovery window
- **THEN** exactly one upstream connection is opened on that owner
- **AND** the original continuation anchor is retained

#### Scenario: One window expires
- **GIVEN** repeated selections report owner cooldown or backoff without a safe existing replay path
- **WHEN** the original remaining request budget or 30-second recovery window expires
- **THEN** creation stops with the existing refusal and opens no upstream connection

#### Scenario: Cancellation while waiting
- **WHEN** bridge creation is cancelled during required-owner recovery, including repeated cancellation
- **THEN** no upstream connection opens, no selection lease remains, and the singleflight creator is settled

#### Scenario: Fenced singleflight waiter does not inherit recovery
- **GIVEN** another request is creating a session for the same required-owner bridge key and actually enters owner recovery
- **WHEN** a follow-up replacement or a request with a retained response-create attempt is joining that inflight creation, including a join that began before recovery
- **THEN** the joining request SHALL refuse without waiting, replaying or removing its anchor
- **AND** the creator, shared future and eligible waiters SHALL retain their ownership and continue unaffected
- **AND** reuse of an already available compatible same-owner session SHALL remain available

#### Scenario: Ambiguous dispatch is not retried
- **GIVEN** an upstream response-create send was attempted and its sent timestamp was cleared
- **WHEN** owner recovery is considered
- **THEN** its retained attempt count prevents another wait, connection or send

#### Scenario: Reselection crosses the recovery deadline
- **GIVEN** a required-owner recovery window has begun
- **WHEN** a later selection blocks past its deadline or returns success after expiry
- **THEN** creation refuses without opening upstream
- **AND** any acquired selection lease is released

#### Scenario: Cancellation races late-selection lease release
- **GIVEN** selection returns a lease after the recovery deadline and the runtime lock delays its release
- **WHEN** bridge creation is cancelled, including repeated cancellation
- **THEN** cancellation SHALL be deferred until that lease is released
- **AND** singleflight creation SHALL settle without opening upstream

#### Scenario: Outer capacity handling receives a terminal owner refusal
- **GIVEN** the required owner has both a terminal eligibility blocker and local contention
- **WHEN** the bridge returns its established public capacity error
- **THEN** outer handling SHALL NOT infer another capacity wait from the public error
- **AND** owner or anchor changes SHALL require the existing independent verified-full-resend or terminal-retirement guards

#### Scenario: Owner becomes terminal before recovery expiry
- **GIVEN** the required owner first has a positively temporary selection failure
- **WHEN** reselection reports a terminal owner failure before the bounded window expires
- **THEN** selection SHALL stop waiting without starting another capacity window
- **AND** existing independently proven full-resend or terminal-retirement recovery SHALL remain available

#### Scenario: Healthy shared creation preserves follow-up compatibility
- **GIVEN** a healthy owner and one in-flight creation that does not enter recovery
- **WHEN** an eligible follow-up or retained-attempt caller joins through its existing guarded path
- **THEN** it SHALL share the same completed session without an additional connection or response-create attempt from joining

#### Scenario: Verified full history bypasses owner waiting
- **GIVEN** an unattempted continuation whose existing durable full-resend proof and portable projection both permit account-neutral replay
- **WHEN** the owner is unavailable in cooldown or error backoff
- **THEN** the existing guarded replay SHALL proceed immediately, excluding that owner and dropping only the proven-redundant anchor
- **AND** incomplete or mismatched history SHALL NOT gain this replay

#### Scenario: Selection churn stays bounded
- **GIVEN** repeated temporary provider-owner failures with a 30-second recovery allowance
- **WHEN** the owner remains unavailable
- **THEN** selection SHALL run at most six times in that window without renewing its deadline
- **AND** production selection-input caching and change-only persistence SHALL remain unchanged

#### Scenario: Local capacity keeps each joiner's dispatch provenance
- **GIVEN** a healthy or not-yet-classified creator receives a positive local-capacity refusal
- **WHEN** a follow-up without an upstream attempt shares that refusal
- **THEN** it SHALL retain the existing outer capacity wait
- **AND** a joining request with a retained response-create attempt SHALL remain final without another wait or replay
- **AND** neither joining request SHALL cancel or release the creator's work
