# Propagate source timeouts through SSE keepalives

## Why

The keepalive injector treats a source TimeoutError as expiry of its own idle
timer. It then retains the already-failed source task and can emit heartbeats
without suspending, starving the proxy event loop instead of ending the stream.

## What Changes

- Distinguish the injector's idle deadline from a source failure.
- Honor an already-settled source outcome when it races with idle expiry.
- Preserve genuine idle heartbeats, callback counts, framing and owned cleanup.

## Impact

- Capability: `responses-api-compat`.
- Production source: `app/core/utils/sse.py`.
- Regression surface: the async iterator and Starlette streaming response.
- Adapt the focused source/test contract from Soju06/codex-lb PR #2681 without
  transplanting that PR's dependency changes.
- No settings, dependencies, account policy, routes, replay policy, receive
  budgets or existing PR22 timeout-attribution changes.
