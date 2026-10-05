# Add keyless priority service-tier opt-in

Deployments that run with proxy API-key authentication disabled (`api_key_auth_enabled = false`) have no per-key `enforced_service_tier` to apply. Their local Codex clients do not reliably ask for Fast on their own: the Codex app heartbeat sends `default` (or no tier), and a restored conversation can come back without the saved Fast setting. Operators of such a deployment need one durable, explicit switch that makes this proxy request the `priority` tier for its keyless traffic.

## Scope

- Add the dashboard setting `keyless_priority_service_tier` (`keylessPriorityServiceTier` on the wire), persisted in `dashboard_settings`, default `false`, managed through `GET`/`PUT /api/settings`, with a forward Alembic revision and downgrade.
- When it is on, a keyless request routed to a subscription account whose service tier is absent, `null`, blank, `auto` or `default` is forwarded upstream with `service_tier: "priority"`. This applies to `/backend-api/codex/responses`, `/v1/responses`, `/v1/chat/completions`, both compact routes and native WebSocket `response.create` frames, including requests that reach upstream through the HTTP session bridge.
- Log the substitution on the origin (`keyless_priority_service_tier_applied`) with the client's original tier.

## Non-goals

- Requesting `priority` does not guarantee upstream serves it. The upstream-reported tier stays the actual tier.
- Do not override API-key policy (any keyed request), the global `prohibit_fast_mode` veto, an explicit client tier (`priority`, `fast`, `flex`, …), the model-catalog tier fallback, subscription-overflow tier stripping, or the origin's signed tier on owner-forwarded bridge requests.
- Do not send the tier to OpenAI-compatible model sources.
- Do not change the account-selection algorithm, usage settlement, model or reasoning-effort handling, cache, transport, auth or credential rules. The substituted tier feeds the existing tier-eligibility filter exactly as a client Fast request does (see `context.md`, "Account narrowing").
- No environment variable and no dashboard UI control in this change. The setting is API-managed until the settings page is rebuilt from frontend source.
