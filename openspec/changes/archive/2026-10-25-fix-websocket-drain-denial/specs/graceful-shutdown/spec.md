## MODIFIED Requirements

### Requirement: Graceful drain closes WebSocket admission

Once graceful drain begins, the application MUST reject every new external WebSocket connection before invoking the route handler, with HTTP503 and `Retry-After: 5`. Proxy paths MUST receive an OpenAI error envelope with code `proxy_unavailable`, type `server_error`, and message `Server is draining`; other paths MUST receive `{"detail":"Server is draining"}`. A Responses WebSocket scope admitted before the barrier MUST remain tracked until its handler exits. Other WebSocket protocols MUST receive the same late-admission rejection but MUST NOT hold the Responses in-flight counter for their full connection lifetime.

#### Scenario: New WebSocket arrives during drain

- **WHEN** a new WebSocket connection scope arrives after drain has begun
- **THEN** the application rejects the connection with HTTP503, `Retry-After: 5`, and the appropriate JSON body without invoking its route handler
- **AND** the rejected connection does not increase the in-flight count

#### Scenario: WebSocket crosses the drain barrier

- **WHEN** a Responses WebSocket scope is admitted immediately before drain begins
- **THEN** it remains in the in-flight count until its route handler exits
- **AND** shutdown waits for that scope within the configured drain timeout

#### Scenario: Realtime or Live connection predates drain

- **WHEN** a non-Responses WebSocket scope is admitted before drain
- **THEN** it does not hold the Responses in-flight counter
- **AND** normal Uvicorn connection shutdown remains its lifecycle bound
