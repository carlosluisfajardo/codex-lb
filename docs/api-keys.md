# API Key Authentication

API key auth is **disabled by default**. In that mode, only local requests to the protected proxy routes can
proceed without a key; non-local requests are rejected until proxy authentication is configured. Enable it in
**Settings → API Key Auth** on the dashboard when clients connect remotely or through Docker, VM, or container
networking that appears non-local to the service.

When enabled, clients must pass a valid API key as a Bearer token:

```
Authorization: Bearer sk-clb-...
```

## Protected routes

The protected proxy routes covered by this setting are:

- `/v1/*` (except `/v1/usage`, which always requires a valid key)
- `/backend-api/codex/*`
- `/backend-api/transcribe`

## Creating keys

Dashboard → API Keys → Create. The full key is shown **only once** at creation. Keys support optional expiration, model restrictions, and rate limits (tokens / cost per day / week / month).

Keys can also be scoped to specific accounts, so a key draws quota only from the accounts assigned to it:

![API keys with assigned accounts](screenshots/apis-assigned-accounts.jpg)

## Reasoning effort policies

A key can either enforce one reasoning effort, subject to the catalog floor below, or allow a selected
non-empty set of client-requested efforts.
Leave the allowed-efforts selection empty to keep the existing unrestricted behavior. A request that explicitly
sets an effort outside its key's allowlist receives a `403 reasoning_effort_not_allowed` response. For models
without the catalog floor, requests that omit effort retain the key's enforced effort when configured, or
the model or upstream default otherwise.

The policy evaluates the effort selected by the client, including supported model aliases such as `-xhigh`.
Each configured effort is distinct: allowing `high` does not allow `xhigh`, and allowing `max` does not allow
`ultra`. The proxy still rewrites an allowed `ultra` request to the upstream wire value `max`.

For Responses and compact requests whose resolved model advertises `ultra` in the catalog snapshot, the
catalog floor takes precedence after alias resolution, allowlist authorization, and key enforcement. Omitted
effort and accepted lower efforts, including a lower effort enforced by the key, become `ultra` and are sent
upstream as `max`. Models that do not advertise `ultra`, or requests without a catalog snapshot, keep their
existing effort handling.

![API key reasoning-effort policy](screenshots/apis-reasoning-efforts.jpg)

For wiring keys into each client, see [Client Setup](client-setup.md).

---

*Specs: [api-keys](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/api-keys), [responses-api-compat](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/responses-api-compat)*
