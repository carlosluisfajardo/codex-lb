## Why

Actual native HTTPS and WSS certificate failures lose their typed cause while traversing error wrappers, so IPC incorrectly marks them eligible for retry. This violates the existing native-egress contract in [outbound-http-clients](../../specs/outbound-http-clients/spec.md) and needs a source correction after the dependency repair.

## What Changes

- Recognize typed Rustls certificate failures inside standard I/O error payloads and ordinary error sources.
- Add durable unit controls and actual-helper HTTPS/WSS regressions for certificate failures and ordinary network failures.
- Preserve trusted TLS/H2, cancellation, pool reuse and decoding behavior.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. The existing native-egress requirement already forbids new replay eligibility for TLS verification failures. This implementation correction changes no normative contract; `skip_specs: true` avoids inventing a spec delta.

## Impact

The shared native error classifier in `crates/codex-lb-egress/src/http.rs`, its unit coverage, and a new actual-helper integration test file. Dependencies, transport policy, CI selection, runtime configuration and the main specification remain unchanged.
