## Current execution custody

The original producer left this partial implementation without an authentic terminal result or retained validation outputs. Its historical execution remains UNKNOWN. On 2026-10-06 Root assigned the existing native Manager one bounded Codex source completion step after the separate same-session reconciliation ended at a weekly capacity limit. Existing implementation checkmarks describe inspected partial artifacts; validation below belongs to this new step and must be proved on its final bytes. Setup retains integration, CI, build, installation and runtime adoption.

## 1. OpenSpec

- [x] 1.1 Write `proposal.md`, `context.md` and the `proxy-runtime-observability` spec delta before behavior edits.

## 2. Setting and gates

- [x] 2.1 Add `wire_capture_file: Path | None = None` to `Settings` and register it as T4 in `SETTING_TIERS`.
- [x] 2.2 Raise `[settings_fields].max` 96 -> 97 with a history line; regenerate `docs/reference/settings.md`.

## 3. Capture module

- [x] 3.1 `app/core/clients/upstream_wire_capture.py`:
  - Private sink with `O_EXCL|O_NOFOLLOW`, mode `0600`, euid-owned private parent.
  - Default-off with no allocation; bounded records and memory; permanent disable on failure.
  - Closed-set and canonical-id validation.
  - Aiohttp trace configs built fresh per call.
  - Bridge note, send, connection registry and settled records.
  - Request-log record and pre-send payload record.
  - Pure `classify_capture`.

## 4. Call sites (owned, non-held)

- [x] 4.1 `http.py:_build_http_client` shared session `trace_configs`.
- [x] 4.2 `codex.py` `create_codex_session`, `_request_via_socks_proxy`, `_open_ws_via_socks_proxy` `trace_configs`.
- [x] 4.3 `conversation_archive.archive_json` pre-send hook before the enable gate.
- [x] 4.4 `request_submit.py`: prepared-dict tier note, post-send record with pre-await receiver snapshot, settled record at `_detach_http_bridge_request`.
- [x] 4.5 `request_log.py:_write_request_log` record before the detached persist task.

## 5. Tests (`tests/unit/test_upstream_wire_capture.py`)

- [x] 5.1 Disabled mode, sink custody (relative path, existing file, symlink, unsafe parent, failure isolation, bounded records).
- [x] 5.2 Value validation with sentinels inside allowed fields and custom stringify traps.
- [x] 5.3 Trace seam (T7):
  - Shared-session trace on the real `_build_http_client`.
  - `archive_json` pre-send record plus POST hop through the real `stream_responses` against a loopback server.
  - Websocket upgrade 101 and the 426 HTTP fallback.
- [x] 5.4 Websocket open contract (T8): direct `connect_responses_websocket` returns a weakref-able upstream whose `upstream_proxy_route_mode` is `direct`.
- [x] 5.5 Bridge: prepared note and first-send tier, retry `not_observed`, receiver snapshot and connection reference, settled record with replay alias.
- [x] 5.6 Q4 handoff:
  - Real `ProxyService._stream_once` into the real `_write_request_log` hook (non-bridge actual tier).
  - Real held `_finalize_websocket_request_state` into the hook (bridge actual tier, replay alias id).
- [x] 5.7 Classifier: complete single-send bridge chain and conservative direct/ambiguous chains; normalized returned-tier and cross-conversation connection regressions.
- [x] 5.8 Red-for-the-right-reason before green, and mutation checks on protective negatives.

## 6. Validation

- [x] 6.1 Run the lint, typecheck, OpenSpec and test gates:
  - `uv run ruff check .`
  - `uv run ruff format --check .`
  - `uv run ty check`
  - the architecture checks, including `scripts/check_settings_tiers.py`
  - `python3 .github/scripts/check_simplicity_budgets.py`
  - `npx @fission-ai/openspec@1.11.0 validate --specs`
  - `npx @fission-ai/openspec@1.11.0 validate --type change --strict add-upstream-wire-capture`
  - the affected unit suites
