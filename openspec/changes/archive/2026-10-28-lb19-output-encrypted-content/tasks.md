## Verification and implementation

- [x] Pin acceptance and unchanged forwarding of `output.encrypted_content` in the request model and on `/backend-api/codex/responses` and `/v1/responses`, and observe the pins red for the right reason before changing production.
- [x] Pin rejection of unknown include values, alone and beside an allowlisted value, with the existing 400 `include` envelope.
- [x] Add only `output.encrypted_content` to the include allowlist, in sorted order.
- [x] Make production accept unknown values once, observe the rejection pins red, restore the source exactly and rerun green.
- [x] Run the focused request and route tests, Ruff, ty and strict OpenSpec validation.
- [x] Synchronize the canonical requirement and reference include list, then archive after verification.
