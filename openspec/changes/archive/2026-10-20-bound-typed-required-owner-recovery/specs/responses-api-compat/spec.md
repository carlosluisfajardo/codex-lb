## ADDED Requirements

### Requirement: Bounded pre-dispatch required-owner recovery

First HTTP bridge creation MAY wait only when selection positively identifies cooldown, error backoff or local contention for the same required owner. Its total recovery window SHALL expire at the earlier of the original request deadline and 30 seconds after the first temporary failure. Reselection SHALL NOT renew that window, SHALL itself be bounded by its deadline, and SHALL NOT accept a success that returns after expiry. Outer capacity handling and follow-up replacement SHALL NOT start another recovery window. An exhausted recovery window, a mismatched selected owner, or a retained response-create attempt SHALL remain final without replay or anchor removal. Ordinary no-account refusals SHALL preserve the existing guarded verified-full-resend and request-path terminal-owner retirement policies; the absence of temporary provenance SHALL NOT itself revoke those independent proofs. Replay-rejection attribution SHALL remain available even when a final refusal forbids replay. Every reselection SHALL retain the required owner, forbid fallback and preserve the previous-response anchor. A retained response-create attempt count greater than zero SHALL forbid this recovery even when the sent timestamp is absent. Unknown and terminal-for-recovery failures SHALL leave required-owner selection promptly without a capacity wait; any existing guarded recovery SHALL remain subject to its independent proof requirements. Recovery SHALL use the existing scheduler and lifecycle ownership, release failed-selection leases before sleeping, and settle singleflight creation and lease ownership on cancellation without dispatching upstream.

#### Scenario: Temporary owner recovers
- **GIVEN** the required owner is temporarily in cooldown before any upstream dispatch
- **WHEN** it becomes selectable within the recovery window
- **THEN** exactly one upstream connection is opened on that owner
- **AND** the original continuation anchor is retained

#### Scenario: One window expires
- **GIVEN** repeated selections report temporary owner contention
- **WHEN** the original remaining request budget or 30-second recovery window expires
- **THEN** creation stops with the existing refusal and opens no upstream connection

#### Scenario: Cancellation while waiting
- **WHEN** bridge creation is cancelled during required-owner recovery, including repeated cancellation
- **THEN** no upstream connection opens, no selection lease remains, and the singleflight creator is settled

#### Scenario: Fenced singleflight waiter does not inherit recovery
- **GIVEN** another request is creating a session for the same required-owner bridge key
- **WHEN** a follow-up replacement or a request with a retained response-create attempt would join that inflight creation
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
