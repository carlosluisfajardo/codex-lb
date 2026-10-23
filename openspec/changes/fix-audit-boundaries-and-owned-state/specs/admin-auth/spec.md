## ADDED Requirements

### Requirement: Trusted dashboard identity uses the preserved socket peer
Both trusted-header sanitization and identity resolution MUST base proxy trust on the preserved raw socket peer throughout the complete application middleware stack.
#### Scenario: Forwarded loopback cannot promote an external socket
- **WHEN** an external socket supplies trusted identity headers and forwarded loopback metadata
- **THEN** dashboard authentication refuses the identity
#### Scenario: Trusted proxy retains external-client identity
- **WHEN** a trusted loopback proxy forwards an external client address with valid identity headers
- **THEN** dashboard authentication retains the trusted identity
