# Output ciphertext include compatibility

## Scope

Responses request validation on `/backend-api/codex/responses` and `/v1/responses`. The normative requirement is `specs/responses-api-compat/spec.md`.

## Incident

Upgraded Codex clients began sending `include: ["output.encrypted_content"]` on ordinary Responses requests, including remote compaction. The allowlist rejected the value, so every such request received the generic 400 `include` envelope during validation, before account selection. A runtime overlay with this one entry was delivered separately; this change carries it into maintained source.

## Decisions

- Allowlist only the exact value clients send. Prefix matching such as `output.*` would accept values no client was observed sending.
- Both HTTP routes and the WebSocket `response.create` path validate through the same request model, so the one entry covers all three. Standalone `/responses/compact` requests use a separate model that does not validate `include`.
- Forwarding is unchanged: an ordinary Responses request carries the value upstream in the client's order. A backend Codex HTTP request that ends in a `compaction_trigger` is still rebuilt into an upstream compact request from a fixed field set that omits `include`, as before; this change only removes the validation rejection that preceded that step.

## Failure modes

- An unknown include value, alone or beside an allowlisted one, is still rejected with HTTP 400 and `param: "include"`.

## Example

`{"model": "gpt-5.1", "instructions": "", "input": [], "include": ["reasoning.encrypted_content", "output.encrypted_content"]}` is forwarded with that `include` array unchanged. Adding `"bogus.value"` to the array returns `{"error": {"message": "Invalid request payload", "type": "invalid_request_error", "code": "invalid_request_error", "param": "include"}}`.
