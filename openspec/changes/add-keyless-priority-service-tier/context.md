# Context: keyless priority service-tier opt-in

Normative requirements live in `specs/responses-api-compat/spec.md`. This file records purpose, decisions, constraints and operations notes.

## Purpose

A local deployment that serves its own Codex clients without proxy API keys has no lever for Fast. Per-key `enforced_service_tier` needs an API key, and the Codex app does not reliably send `priority` itself. Its heartbeat chooses `default` (or omits the tier), and a restored chat can resume without the saved Fast choice. The opt-in lets the operator of such a deployment say once, durably, "request priority for my keyless traffic".

## Decisions

- **Dashboard setting, not environment.** `keyless_priority_service_tier` is a T3 behaviour flag whose management surface is `dashboard_settings`. It follows `prohibit_fast_mode`: NOT NULL, server default false, partial `PUT` semantics, settings-audit coverage, no `Settings` field and no env alias. It is not in `SECURITY_SETTINGS_FIELDS`, matching `prohibit_fast_mode`.
- **Keyless means `api_key is None` after proxy auth.** That only happens when `api_key_auth_enabled` is false and the caller is local or an allowed unauthenticated peer. Requests carrying the required-capability header always need a key. No user-agent, header or auth inference is involved.
- **Applied on subscription routes only, after model-source selection.** The HTTP routes call `apply_keyless_priority_service_tier` inside their existing `if source is None:` block, immediately before `apply_enforced_service_tier_model_fallback`. Native WebSocket calls it in `_prepare_websocket_response_create_request`, because that transport never dispatches to a model source. Compact is subscription-only. Consequences:
  - Direct model sources never receive a proxy-substituted tier. Per-key enforcement differs here: a keyed enforced tier is still forwarded to them.
  - The overflow decision and its exhaustion probe see the same tier that subscription selection uses.
  - Overflow dispatch keeps stripping the tier.
- **Only a tier left to upstream is replaced.** Absent, `null`, blank, `auto` and `default` become `priority`. Any explicit tier (`priority`, `fast`→`priority`, `flex`, …) is left alone, and so are model aliases that already resolve to `priority`. Per-key enforcement differs: a key overrides every client tier, whereas the opt-in is an upgrade of "no preference".
- **The global veto wins.** With `prohibit_fast_mode` on, the helper does nothing, so keyless forwarding is byte-for-byte the pre-change behaviour, including literal `auto`/`default`.
- **One policy snapshot per HTTP request.** Capture the veto and keyless opt-in from the same dashboard settings row before awaited selection. Compact captures both inside its shared handler. An atomic change from opt-in off/veto off to opt-in on/veto on cannot combine the old veto with the new opt-in. WebSocket retains its existing connection snapshot.
- **Provenance feeds the catalog fallback.** A substituted tier is reported as policy-supplied (`service_tier_was_enforced = True`). `apply_enforced_service_tier_model_fallback` can therefore drop it for a model the authoritative catalog says lacks `priority`, instead of letting account selection fail.
- **The origin decides; owners never re-decide.** `internal_bridge_responses` and `_stream_responses` are unchanged. The origin's substituted tier is part of the HMAC-signed forward body, and the owner restores the signed tier before applying only its own prohibit veto.
- **Honest metadata.** On subscription-account rows, `requested_service_tier` is the tier codex-lb sent upstream (now `priority`). `actual_service_tier` is only what upstream reports, and the billable `service_tier` prefers the actual tier. Overflow and model-source rows keep the existing convention: `requested_service_tier` is the post-policy tier that the overflow decision used, while the source request carries no tier. The client's original value is recorded on the origin in the info log line `keyless_priority_service_tier_applied client_service_tier=<value> substituted_service_tier=priority`. That line records the substitution, not the final wire value. A later catalog fallback or veto logs its own line (the fallback reuses the existing `api_key_enforced_service_tier_model_fallback` event name).

## Constraints and failure modes

- **Upstream may not serve priority.** Upstream can return `default` for a request that asked for `priority`. The logs then show requested `priority` and actual `default`, and the billable tier and cost follow `default`. If upstream omits the tier entirely, `actual_service_tier` stays null and the existing fallback bills the requested tier (`priority`). That is the same treatment a client-sent or key-enforced `priority` already gets.
- **Account narrowing.** A `priority` tier narrows account selection to accounts or plans that the catalog says support the model at that tier. This is the same behaviour a client asking for Fast gets. The selection algorithm is unchanged; only its tier input changes. The catalog fallback drops the substituted tier only when no account or plan advertises `priority` for the model. In a mixed pool, where authoritative per-account catalogs disagree about `priority` for the same model, turning the opt-in on has these effects:
  - Keyless traffic for that model concentrates on the priority-capable accounts, and idle non-priority accounts are not used for it.
  - When those accounts are exhausted, requests fail selection (`NO_PLAN_SUPPORT_FOR_MODEL`) or overflow.
  - A conversation whose continuity owner lacks `priority` can fail its next turn with `CONTINUITY_OWNER_POLICY_CONFLICT`.
  - HTTP bridge sessions bound to such an account are not reused.

  Enable the opt-in when the pool's accounts serve `priority` for the models in use, or accept these effects. Whether a given deployment's pool is mixed is an operator fact this change does not check.
- **WebSocket timing.** Native WebSocket reads the setting once per connection, the same as `prohibit_fast_mode`. A toggle reaches an already-open socket only after the client reconnects. The upgraded tier also feeds the connect-time routing hint `;tier=priority`.
- **Rolling upgrades.** Replicas running older code ignore the column, and the server default keeps their inserts valid. The settings cache refreshes within its TTL; an origin and an owner that briefly disagree still converge on the origin's signed tier.
- **No UI yet.** The shipped settings page neither shows nor sends the field. A UI save keeps the stored value, because the server merges omitted fields from the current row.

## Maintained proof and recovery attribution

The completed original Claude producer supplied the implementation and provider-free stdlib proof. A fresh independent review found the HTTP mixed-row race and that the root-level proof was omitted by the normal unit check and polluted shared test settings when imported. Codex-lb manager corrected only those two findings in an isolated full upstream clone after the separate author correction was signal-interrupted without an accepted final result. The interrupted run's partial source and evidence were preserved and not used as input.

`tests/unit/test_keyless_priority_contract.py` is collected by the existing unit check and runs `tests.fixtures.keyless_priority_contract` in a dedicated process. The child owns its synthetic SQLite, environment, loopback-only guards and upstream stubs; parent pytest configuration stays intact. Tests for all five HTTP paths failed for unwanted `priority` injection before the snapshot fix and passed afterward. Retained original migration, WebSocket, keyed-policy and metadata evidence remains attributed to its author. PostgreSQL, live routing and browser acceptance remain separate.

This candidate is based on official `bb4db9d08cc955743735678ccdb8d8bef19f7bea`. Integrating it with a later upstream release or the independent account-lifecycle migration requires a reviewed single-head migration graph before installation. Preserve the baseline build and database backup; disabling the default-off flag is the first behavioral rollback. No source proof guarantees that upstream serves a requested priority tier.

## Operations example

Enable on a keyless local deployment, then confirm what was requested and what upstream returned:

```bash
curl -sS -X PUT http://127.0.0.1:2455/api/settings \
  -H 'content-type: application/json' \
  -d '{"keylessPriorityServiceTier": true}'
curl -sS http://127.0.0.1:2455/api/settings | jq .keylessPriorityServiceTier   # true
# After the next Codex turn:
curl -sS 'http://127.0.0.1:2455/api/request-logs?timeframe=1h&limit=5' \
  | jq '.requests[] | {requestedServiceTier, actualServiceTier, serviceTier}'
```

HTTP requests pick the change up within the settings-cache TTL. Open native WebSocket sessions keep the value they connected with, so the Codex app must reconnect, for example after a restart, before enabling takes effect or before disabling stops priority.

Disable with `{"keylessPriorityServiceTier": false}`. To roll the schema back, do it before reinstalling a build without this revision. An older build refuses to start against a database stamped at `20261005_000000_add_dashboard_keyless_priority_service_tier`, and its scripts cannot downgrade a revision they do not know.

With this build still installed and the service stopped, downgrade to `20260912_000000_merge_thread_cache_and_bridge_retirement_heads`, which drops only this column:

```bash
python -c "from alembic import command; from app.db.migrate import _build_alembic_config; command.downgrade(_build_alembic_config('<database-url>'), '20260912_000000_merge_thread_cache_and_bridge_retirement_heads')"
```

Alternatively, restore the SQLite pre-migration backup.
