## Context

See [proposal.md](proposal.md) for the defect and [the existing native-egress contract](../../specs/outbound-http-clients/spec.md) for the required replay boundary. Tokio-rustls wraps certificate-processing errors in `std::io::Error`; the current classifier follows only `Error::source()`, which can skip an I/O error's stored typed payload.

## Goals / Non-Goals

Restore certificate provenance for native HTTPS and WSS without changing the error envelope or ordinary connection retry eligibility. Preserve the dependency repair and all existing TLS, pool, cancellation and decoder settings. This stage does not demonstrate a routed POST replay, change Python policy, fix unrelated native timeouts, or alter hosted test selection.

## Decisions

- Inspect the actual wrapped errors before implementation and retain their synthetic-loopback diagnostic evidence.
- Traverse an I/O error through `get_ref()` so its stored cause is inspected before following ordinary `Error::source()` links. Continue recognizing only typed `rustls::Error::InvalidCertificate` values. String matching and classifying every I/O or TLS error as a certificate failure would incorrectly suppress ordinary retries.
- Pin nested wrappers and distinct certificate causes in unit tests; keep message-only, unrelated Rustls and ordinary network errors negative. Use actual-helper HTTPS/WSS tests to prove IPC flags, trusted connections and ordinary connection failures.
- For example, `io::Error(InvalidData, rustls::Error::InvalidCertificate(UnknownIssuer))` must yield TLS verification `true` and retry eligibility `false`; a similarly worded `rustls::Error::General` must remain untyped.

## Risks / Trade-offs

- Incorrect broad classification could suppress valid endpoint fallback. Protective unit and wire controls retain ordinary failure eligibility.
- Helper tests can run against a stale executable. Local qualification records the selected executable hash alongside source identities and the final freeze; no test builds a helper implicitly.
- The existing hosted Rust step explicitly selects four older Python files. The new helper regression is qualified locally with an explicit binary; general discovery without that binary uses the existing skip convention. CI workflow changes are outside this stage.
- Full native-suite and live runtime parity remain separate from the typed-failure correction. The five prior local timeout failures are not attributed to this change without causal evidence.
