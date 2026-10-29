## ADDED Requirements

### Requirement: SSE keepalives distinguish idle deadlines from source failures

The SSE keepalive injector MUST emit a heartbeat only when its own idle timer
expires while the pending source read remains unsettled. A TimeoutError raised
by the source MUST propagate as the original exception rather than becoming a
heartbeat. If the idle timer expires while the source has already settled, the
injector MUST honor the source chunk, exhaustion or exception before emitting a
heartbeat. A suppressed heartbeat MUST NOT invoke the heartbeat callback.

The injector MUST preserve source payloads and configured heartbeat framing.
When the consumer closes or is cancelled, the injector MUST cancel and settle
its owned source read and close the source iterator. Disabled injection MUST
continue to pass through source outcomes without creating an idle timer.

#### Scenario: Source timeout follows a data block

- **GIVEN** a source has emitted a data block and then raises TimeoutError
- **WHEN** the consumer requests the next block
- **THEN** the original exception propagates and the source is finalized
- **AND** the timeout is not replaced by a heartbeat

#### Scenario: Source fails between consumer reads

- **GIVEN** a genuinely idle source caused one heartbeat
- **AND** that source fails with TimeoutError before the consumer reads again
- **WHEN** the consumer requests the next block
- **THEN** the original exception propagates without another heartbeat

#### Scenario: Source settlement races with the idle deadline

- **GIVEN** the idle timer expires and the source settles before the waiter resumes
- **WHEN** the injector handles the deadline
- **THEN** it honors the settled chunk, exhaustion or exception
- **AND** it does not emit a heartbeat or invoke its callback

#### Scenario: Consumer cancellation owns source cleanup

- **GIVEN** a source read remains pending after an idle heartbeat
- **WHEN** the consumer cancels its next read
- **THEN** the owned source read is cancelled and settled and the iterator is closed
