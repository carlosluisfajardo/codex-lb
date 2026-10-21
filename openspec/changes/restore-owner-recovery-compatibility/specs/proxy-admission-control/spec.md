## MODIFIED Requirements

### Requirement: HTTP bridge startup admission waits are bounded

The proxy MUST apply the fixed 10-second proxy admission wait timeout to each per-session response-create gate acquisition attempt. A waiter on a request-owned bridge creation SHALL use the earlier of its own original request deadline and the creator's original request deadline. Legacy handoff waits without a creation deadline retain the fixed admission timeout, clamped to the waiter's remaining request budget.

For per-session response-create gate acquisition by a bridged Responses request, an expired gate acquisition attempt MUST be treated as a recoverable capacity wait rather than a terminal failure: the request MUST release its queue slot and account lease, wait with capacity-wait progress semantics, and retry gate acquisition, bounded by the bridge request budget. Requests eligible for soft-affinity reroute MUST still attempt the reroute before entering the recoverable wait. When the bridge request budget is exhausted before the gate opens, the proxy MUST reject the request locally with HTTP 429, `error.code = "response_create_gate_timeout"`, and the stable local-overload reason.

For bridge capacity waiters and in-flight session creation waiters, when the timeout expires the proxy MUST reject the request locally with HTTP 429 and an OpenAI-style `capacity_exhausted_active_sessions` error envelope. A waiter timing out or being cancelled MUST NOT evict, fail or cancel another request's in-flight creation, discard its opened upstream work, or release its lease. The creator retains responsibility for settling its own marker. An independently proven, never-dispatched full-history request joining an owner-selection-recovering creator SHALL retain the immediate guarded replay scenario in responses-api-compat; the creation key alone does not establish a shared logical operation. That replay SHALL NOT settle the creator's future or change its deadline.

If a request owns in-flight bridge session creation and is cancelled or fails after publishing the in-flight marker but before registering the created session, the proxy MUST remove or settle that in-flight marker. If a session owner later finishes creation after its in-flight marker was evicted, the owner MUST NOT return an unregistered bridge session to the caller.

#### Scenario: Gate contention queues within the bridge request budget

- **GIVEN** an HTTP bridge session whose response-create gate is held by a legitimate in-flight turn
- **AND** a bridged Responses request that cannot soft-reroute (hard-affinity key or `previous_response_id` continuity)
- **WHEN** a gate acquisition attempt exceeds the fixed proxy admission wait timeout
- **THEN** the request emits capacity-wait keepalive progress on streaming surfaces and retries gate acquisition
- **AND** the request completes normally once the in-flight turn releases the gate before the bridge request budget expires

#### Scenario: Gate contention still fails once the request budget is exhausted

- **WHEN** a bridged Responses request retries response-create gate acquisition until the bridge request budget is exhausted
- **THEN** the request is rejected locally with HTTP 429
- **AND** the error payload uses `error.code = "response_create_gate_timeout"`
- **AND** no response-create gate lease is recorded on that request state

#### Scenario: Soft-affinity requests reroute before waiting

- **GIVEN** a bridged Responses request with a soft-affinity session key and no `previous_response_id`
- **WHEN** its first gate acquisition attempt times out
- **THEN** the proxy attempts the internal soft-affinity reroute to a fresh bridge session
- **AND** the recoverable gate wait applies only when reroute is not permitted

#### Scenario: Stuck sessions are still detected between attempts

- **WHEN** a gate acquisition attempt times out while a pending bridge request has been stuck past the stuck-gate retirement threshold
- **THEN** the stuck session retirement check still runs on that attempt

#### Scenario: In-flight bridge session creation does not finish

- **WHEN** a bridged Responses request waits on another request's in-flight session creation
- **AND** the in-flight creation does not finish before the waiter's applicable creation/request deadline
- **THEN** the waiter is rejected locally with HTTP 429 and `error.code = "proxy_overloaded"`
- **AND** the creator and other waiters retain their own creation, deadline and lease ownership

#### Scenario: Bridge capacity waiter does not make progress

- **WHEN** the HTTP bridge is at capacity and a request waits for in-flight bridge work to free capacity
- **AND** no capacity becomes available before the waiter's applicable creation/request deadline
- **THEN** the waiter is rejected locally with HTTP 429 and `error.code = "proxy_overloaded"`

#### Scenario: In-flight owner is cancelled during stale session close

- **WHEN** a bridge session creation owner has published an in-flight marker
- **AND** it is cancelled while closing a stale local bridge session before creating the replacement session
- **THEN** the in-flight marker is removed or settled
- **AND** later requests do not remain blocked on that cancelled owner's future

### Requirement: Admission waits on shared futures scale O(1) per waiter

When multiple requests wait on one shared future (an inflight bridge session creation, a capacity slot, a token-refresh singleflight, or a usage-refresh singleflight), the system MUST use the established shared-future fan-out wait mechanism so attaching a waiter, a waiter timing out, and a waiter being cancelled each perform O(1) work on the shared future. The shared future MUST carry a constant number of done callbacks regardless of waiter count, and the wait mechanism itself MUST NOT cancel or otherwise mutate the shared future or the work it represents when a waiter times out or is cancelled. HTTP bridge admission handlers SHALL NOT settle or unregister another request's pending creation because a waiter timed out; only the creation owner settles its own work. The shared future's result, exception, or cancellation MUST propagate to every waiter with the same semantics as `asyncio.wait_for(asyncio.shield(shared), timeout)`.

The same bounded-callback contract applies to waits that re-attach to one owned task repeatedly: defer-cancellation waits on owned cleanup tasks, repeated timed waits such as SSE keepalive ticks on a pending chunk task, and bounded teardown drains. A defer-cancellation wait MUST shield itself from level-cancelled scopes so re-delivered cancellation cannot busy-spin the wait loop, MUST keep the owned task's done-callback count bounded by a constant regardless of how many times the waiter is cancelled or times out, MUST NOT cancel the owned task, MUST defer the caller's cancellation until the owned task finishes and then surface it, and MUST propagate the owned task's cancellation and exceptions unchanged.

Every defer-cancellation wait MUST route through the one canonical shared-future helper (or, where site-specific control flow forces an inline loop, wait through the shared-future fan-out mechanism inside that loop) rather than a hand-rolled `asyncio.shield` retry. The deferred-cancellation surfacing above applies uniformly: a caller consuming a boolean or exception marker from any defer-cancellation wait MUST receive the marker for a level-cancelled scope as well as for edge task cancellation, so cleanup-then-cancel sequencing does not depend on which copy of the wait a call site reached.

#### Scenario: Waiter pile-up keeps the shared future's callback list constant

- **WHEN** many requests wait on the same inflight bridge-session future
- **THEN** the shared future carries a constant number of done callbacks
- **AND** the callback count does not grow with the number of waiters

#### Scenario: Mass timeout does not degrade the event loop

- **GIVEN** waiters piled onto a shared future that has not resolved within
  the admission wait timeout
- **WHEN** the waiters time out together
- **THEN** each timeout detaches in O(1) without scanning the shared future's
  callback list
- **AND** the surviving admission contract (local-overload `429` with the
  capacity error code) is unchanged

#### Scenario: Client-disconnect storm leaves the owner's creation running

- **WHEN** every waiter on an inflight session future is cancelled by client
  disconnects
- **THEN** the shared future stays pending and the owner's session creation
  continues
- **AND** no per-waiter callbacks remain attached to the shared future

#### Scenario: Cancelled usage-refresh waiters leave shared refresh running

- **GIVEN** many callers are waiting on one in-flight usage refresh
- **WHEN** all but one caller are cancelled
- **THEN** the cancelled callers detach without adding or removing per-waiter
  callbacks on the shared refresh task
- **AND** the shared refresh continues to completion for the remaining caller

#### Scenario: Non-joining usage refresh starts after its predecessor

- **GIVEN** a usage refresh is already in flight for an account
- **WHEN** another caller requests a non-joining refresh for that account
- **THEN** it waits without cancelling or mutating the in-flight refresh
- **AND** it starts a successor refresh only after the in-flight refresh has
  finished

#### Scenario: Level-cancelled scope cannot spin a defer-cancellation wait

- **GIVEN** a cleanup task owned by a defer-cancellation wait is still running
- **WHEN** the waiter's scope is level-cancelled (client disconnect) while the
  cleanup task remains pending
- **THEN** the wait loop does not busy-spin on re-delivered cancellation
- **AND** the cleanup task's done-callback count stays bounded by a constant
- **AND** the cleanup task runs to completion before the caller's cancellation
  is surfaced

#### Scenario: Keepalive ticks leave the pending chunk task's callbacks bounded

- **GIVEN** an SSE stream whose upstream produces no chunk for many keepalive
  intervals
- **WHEN** each keepalive tick's timed wait on the pending chunk task times out
- **THEN** keepalive frames are emitted and the pending chunk task is not
  cancelled
- **AND** the pending chunk task's done-callback count does not grow with the
  number of elapsed ticks

#### Scenario: Every defer-cancellation wait shares the canonical implementation

- **WHEN** any module performs a defer-cancellation wait on an owned task
- **THEN** the wait routes through the canonical shared-future helper (or the
  shared-future fan-out mechanism inside a site-specific loop)
- **AND** no hand-rolled `asyncio.shield` retry loop remains

#### Scenario: Level cancellation surfaces through every marker shape

- **GIVEN** a caller in a level-cancelled scope awaiting an owned cleanup via
  a defer-cancellation wait that reports a boolean or exception marker
- **WHEN** the owned cleanup completes
- **THEN** the marker reports the deferred cancellation
- **AND** the caller can re-raise it deterministically after cleanup instead
  of being interrupted at an arbitrary later checkpoint
