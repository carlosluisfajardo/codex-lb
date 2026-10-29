# Attribute active HTTP SSE total expiry correctly

## Why

Python HTTP SSE parsing renews its receive-idle wait for every body chunk, but
generic timeout attribution currently remembers only headers and complete SSE
blocks. An overall deadline during a slowly fragmented event can therefore be
reported as upstream idle despite recent upstream bytes.

## What Changes

- Share received-byte activity with HTTP Responses timeout attribution.
- Preserve the existing idle/overall budgets, genuine-idle tie preference,
  transport errors, cancellation, cleanup, identity and replay restrictions.
- Pin incomplete-event, complete-event and genuine-idle behavior using fake
  transports and the repository virtual scheduler.

## Impact

- Affected capability: `responses-api-compat`.
- Affected source: `app/core/clients/proxy.py` and focused streaming tests.
- No settings, dependencies, routes, schemas, retry policy or native protocol changes.
