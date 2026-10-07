## 1. Contract

- [x] 1.1 Record the oversized replayed `function_call.arguments` failure and the native request-rejection delivery contract.

## 2. Implementation

- [x] 2.1 Reproduce both failures at the product boundary: the bridge route, the bridge-off route, and the WebSocket route.
- [x] 2.2 Reject replayed `function_call.arguments` above 1,048,576 characters before admission, reservation and upstream work, on HTTP stream, HTTP collect and WebSocket.
- [x] 2.3 Deliver native upstream request rejections as HTTP `400` before commit and as `response.failed` plus `[DONE]` after commit, with sanitized text.
- [x] 2.4 Pin settlement: one error row, the reservation released, no account-health penalty, no replay.

## 3. Verification and delivery

- [x] 3.1 Run focused regression tests, lint, type check and the affected suites.
- [x] 3.2 Validate the change with `openspec validate --strict`.
- [ ] 3.3 Sync the delta into `openspec/specs/responses-api-compat/` and archive after merge.
