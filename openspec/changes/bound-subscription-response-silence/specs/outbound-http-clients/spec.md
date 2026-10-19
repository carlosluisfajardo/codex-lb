## ADDED Requirements

### Requirement: Routed subscription HTTP preserves phase timeout budgets
Routed subscription HTTP MUST carry the same effective total, connect and response-header idle budgets as direct HTTP. Successful headers MUST NOT shorten the remaining overall stream budget to the header allowance. Body transport activity MUST renew the existing body idle allowance. An expired header or body idle wait MUST terminate and release its attempt resources without acquiring new replay rights.

#### Scenario: Routed request accepts connection but sends no headers
- **GIVEN** an established routed subscription HTTP connection with no response headers
- **WHEN** the effective header idle allowance elapses before the overall budget
- **THEN** the request terminates on the phase allowance
- **AND** no alternate account or route is selected merely because of the timeout

#### Scenario: Active body outlives the header allowance
- **GIVEN** response headers received within the phase allowance
- **WHEN** body activity continues within each idle interval
- **THEN** the request can complete after multiple idle intervals within its original overall budget
