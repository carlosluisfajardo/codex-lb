# Deliver upstream request rejections to native Codex clients

## Why

The installed runtime carries an in-place patch, delivered here by commit
`46cc80b8`, that changes how the native Codex HTTP route delivers an upstream
request rejection. On the HTTP bridge, before the response commits, the native
client now gets the HTTP `400` an HTTP upstream returns. Otherwise it gets a
terminal `response.failed`. Previously it got a bare `error` frame on an HTTP
`200`.

The patch came from the change `oversized-history-terminal-errors` (source
commit `9ee8e0c7`). Only its four production files were applied, so its
specification and its test update were left behind:

- The main `responses-api-compat` spec still says native error frames pass
  through unchanged.
- `test_backend_responses_http_bridge_fails_a_self_excluded_hard_owner_replay_closed[model_fallback]`
  still expects the bare `error` frame on a `200`, and fails against the
  delivered code with `assert 400 == 200`.

## What Changes

- Modify three `responses-api-compat` requirements using the original change's
  text, unchanged:
  - "Preserve raw backend stream error frames when contract mode is disabled";
  - "Typeless terminal errors retain settlement and correlation data";
  - "Native HTTP interprets Responses stream events compatibly".

  The current text of these three requirements is byte-identical to the base
  the original change modified.
- Correct the model-fallback expectation of that test to the original contract:
  HTTP `400` with a JSON error envelope, code `invalid_request_error`, and the
  fixed unsupported-model message. The test's other assertions are unchanged:
  one re-selection, no alternate account, no hidden owner reconnect, a single
  upstream send, and `503 hard_affinity_saturated` for the created-only shape.

## Not Included

- The original change's added requirement for replayed `function_call`
  arguments above the upstream string limit.
- The original change's dedicated request-rejection and argument-limit tests.
- No production code changes.

## Capabilities

### Modified Capabilities

- `responses-api-compat`

## Impact

- Specs: `responses-api-compat` (three modified requirements).
- Tests: `tests/integration/test_http_responses_bridge.py` (one assertion block).
- Code, configuration, schema: none.
