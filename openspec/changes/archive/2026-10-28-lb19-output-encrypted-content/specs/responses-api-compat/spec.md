## ADDED Requirements

### Requirement: Responses include values follow the documented allowlist

The service MUST validate every `include` entry of a `/backend-api/codex/responses` or `/v1/responses` request against the documented include allowlist, and that allowlist MUST contain `output.encrypted_content` and `reasoning.encrypted_content`. When such a request is forwarded upstream as a Responses request rather than rebuilt into an upstream compact request, those entries MUST reach the upstream payload unchanged and in the client's order. A request with any entry outside the allowlist MUST be rejected with HTTP 400 and the envelope `{"error": {"message": "Invalid request payload", "type": "invalid_request_error", "code": "invalid_request_error", "param": "include"}}`, including when allowlisted entries share the array.

#### Scenario: Output ciphertext include is forwarded unchanged

- **WHEN** a client sends `/backend-api/codex/responses` or `/v1/responses` with `include` `["output.encrypted_content"]`
- **THEN** the proxy accepts the request
- **AND** the upstream payload carries `include` `["output.encrypted_content"]`

#### Scenario: Output and reasoning ciphertext includes are forwarded together

- **WHEN** a client sends `include` `["reasoning.encrypted_content", "output.encrypted_content"]` on either route
- **THEN** the upstream payload carries both entries in the client's order

#### Scenario: An unknown include value is rejected beside an allowlisted one

- **WHEN** a client sends `include` `["bogus.value"]` or `["output.encrypted_content", "bogus.value"]` on either route
- **THEN** the proxy responds with HTTP 400 and the `include` invalid-request envelope
