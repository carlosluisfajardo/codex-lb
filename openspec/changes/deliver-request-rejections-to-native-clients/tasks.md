## 1. Specification

- [x] 1.1 Modify the three `responses-api-compat` requirements with the original
  `oversized-history-terminal-errors` text (source commit `9ee8e0c7`), unchanged.

## 2. Regression coverage

- [x] 2.1 Correct the model-fallback expectation of
  `test_backend_responses_http_bridge_fails_a_self_excluded_hard_owner_replay_closed`
  to HTTP `400`, code `invalid_request_error` and the fixed unsupported-model
  message, keeping its hard-owner, single-send and created-only assertions.

## 3. Validation

- [x] 3.1 The corrected test passes on the delivered code, and fails when the
  native request-rejection delivery is disabled.
- [x] 3.2 Ruff check and format, architecture checks and `ty check`.
- [x] 3.3 `openspec validate deliver-request-rejections-to-native-clients --strict`
  and `openspec validate --specs`.
