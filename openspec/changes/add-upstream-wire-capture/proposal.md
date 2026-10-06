## Why

A completed CLI pilot recorded `requested_service_tier=priority` and `actual_service_tier=default` in `request_logs`, but nothing on the LB shows what tier actually left the process on the upstream hop, which hop carried it, or what status that hop returned. The existing `upstream_summary` trace channel logs account ids, error text and payload summaries, its success websocket status is synthetic, and the maintained service launcher discards stdout/stderr, so it cannot answer that question. One new labelled ordinary priority request needs a reviewed, metadata-only, default-off capture before it is issued.

## What Changes

- **New module** `app/core/clients/upstream_wire_capture.py`. When the new T4 setting `wire_capture_file` (`CODEX_LB_WIRE_CAPTURE_FILE`) names an absolute path, it appends validated metadata records to a new private JSONL file (`O_CREAT|O_EXCL|O_NOFOLLOW`, mode `0600`, parent owned by the effective user with no group/other bits). Unset (the default) allocates no sink, trace or hook state. Any capture failure permanently disables capture and never changes a request outcome. Records are at most 2048 bytes. The sink identity is checked before every write.
- **Value validation at every boundary.** Tier, request kind, transport, method, route mode, status and scheme come from closed sets. Identifiers are recorded only in canonical UUID, `ws_`/`http_prewarm_` + 32 hex, or `resp_` + alphanumeric form. Anything else becomes `null` plus a fixed state (`absent`, `unrecognized`, `noncanonical`, `not_observed`) and is never stringified or serialized.
- **Observation seams, all outside held paths:**
  - **aiohttp trace hooks** (`TraceConfig`). These sit on the shared session (`app/core/clients/http.py`) and on routed/SOCKS sessions (`app/core/clients/codex.py`). They record the method, scheme, `codex_responses` path class, HTTP version, the routing-hint `tier=` token, the final response status and the redirect count of each upstream Responses request.
  - **Pre-send payload hook** at the top of `conversation_archive.archive_json`, which runs before the archive enable gate. It records the transport, method and the `service_tier` field of the exact object the core client is about to send.
  - **HTTP bridge hooks** in `request_submit.py`:
    - A tier note on the prepared `response.create` dict.
    - A post-send record with a snapshot of the send receiver and a monotonic connection reference.
    - A request-state snapshot at `_detach_http_bridge_request` carrying the bridge response id, replay-alias presence, request kind, attempt count and normalized actual tier, without claiming that this snapshot itself proves terminal settlement.
  - **Request-log hook** at the start of `_write_request_log`. It records the request-log response id with an explicitly unresolved origin, plus requested/actual tier, status, kind and transport.
- **Conservative pure classifier** `classify_capture(records, conversation_id=...)`. It reports a tier verdict only from a single eligible attempt with proven local send and an at-origin tier. Every ambiguity is `PARTIAL`/`UNKNOWN`, never `PRIORITY_NOT_SENT` or `TIER_OMITTED`.
- **Gates.** The settings-field budget rises 96 → 97 and the generated settings reference is regenerated.

## Why not a default (P2)

`wire_capture_file` is a per-run private file path for incident debugging. A path cannot have a safe default. Enabling capture by default would write request metadata on every deployment, so it stays opt-in (T4, never in `.env.example`).

## Correction context

This change implements the completed formal design (`CAPTURE-PLAN.json` sha256 `fda166ea8d3f93522fdfd096f9f38eea2b6309abde4375d49d469eab697c1b53`), corrected by the independent qualification (overall ADJUSTED, `REPORT.json` sha256 `bb0dac026040b3fa5df7201d9c39ee3f0a896867c1fcb52701c07303b787f345`). Both originals stay immutable outside this repository; `context.md` records each Q1-Q4 disposition.

One design point changed during implementation. The draft A10 hooks in `app/modules/proxy/_service/http_bridge/mixin.py` are not made, because the architecture ceiling `http_bridge_mixin_lines = 2436` leaves one line of headroom (file is 2435 lines), and the threshold lives in `openspec/specs/proxy-architecture/spec.md`, outside this change. The bridge connection is observed at the send seam instead.

## Deferred (PARTIAL by construction)

- **Bridge opener routing hint and raw returned-tier absence.** The first frame's sender may differ from the connection creator. The service also collapses absent and invalid returned tiers to `None`. These observations remain `not_observed` at the allowed seams. Multiple sends on a connection lack an explicit frame-to-response map and remain PARTIAL.
- **Numeric status of a rejected direct bridge websocket handshake.** This needs the `http_bridge/mixin.py` exception sites (1798/1834/2124), which are blocked by the ceiling above.
- **Upstream response id on the non-bridge path.** This needs `streaming/mixin.py::_stream_once`, which is not in this change and is at its 1100-line ceiling. The request-log id keeps an unresolved origin.
- **Successful send of a raw-websocket direct frame.** This needs held `app/core/clients/proxy.py:3007-3011`. Only the pre-send record and the handshake status are available.
- **Native egress hop status.** These are native-helper internals. The maintained guard pins `PATH` so the helper is not discovered.

## Impact

- **Affected specs:** `proxy-runtime-observability` (ADDED requirements).
- **Affected code:**
  - New: `app/core/clients/upstream_wire_capture.py`.
  - Edited: `app/core/clients/http.py`, `app/core/clients/codex.py`, `app/core/conversation_archive.py`, `app/modules/proxy/_service/http_bridge/request_submit.py`, `app/modules/proxy/_service/request_log.py`, `app/core/config/settings.py`, `app/core/config/tiers.py`.
  - Gates: `.github/simplicity-budgets.toml`, `docs/reference/settings.md`.
- **Tests:** `tests/unit/test_upstream_wire_capture.py` (new).
- **Not affected:** no migration, no `.env.example`/README/CHANGELOG change, and no ceiling-guarded proxy file grows.
- **Ownership:** runtime adoption (build, install, guard opt-in flag) belongs to the integration owner and is not part of this change.
