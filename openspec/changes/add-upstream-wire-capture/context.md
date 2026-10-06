# Context: upstream wire capture

## Purpose

Answer one question for one labelled ordinary priority request: what tier left the LB on the actual upstream hop, over which hop, with which status, and what tier the upstream event reported back. The answer must come only from named, validated metadata at its origin. It is a diagnostic for incident work, not a steady-state feature.

## Origin of each observation

| Record | Seam (non-held) | What it proves |
|---|---|---|
| `capture_open` | `trace_configs()` during `_build_http_client` at startup | the sink opened in this process (pid, app version); nothing about later records |
| `upstream_http_hop` | aiohttp `TraceConfig` on the shared and routed sessions | an aiohttp request to `*/codex/responses` received its final response with this status; redirects are only counted |
| `outgoing_payload` | `conversation_archive.archive_json` (held `proxy.py` calls it with the exact send object at 2996, 4172, 4257) | the `service_tier` field of the object about to be sent; `pre_send` only |
| `bridge_frame_sent` | `request_submit.py::_send_http_bridge_request_text_with_archive_id` after `send_text` returned | local send completion of a `response.create` frame on a connection reference |
| `bridge_request_settled` | `request_submit.py::_detach_http_bridge_request` (called in the held bridge stream `finally`) | request-state snapshot at detach: bridge ref -> response id populated from upstream events (which can include terminal events), replay alias presence, kind, attempt count, normalized actual tier; the record itself does not prove terminal settlement |
| `request_log_row` | `request_log.py::_write_request_log` before the detached persist | the row's id, tiers, status and kind as handed to persistence; the id origin is caller-dependent |

## Corrections from the independent qualification

- **Q1 (values).** Every field is validated from closed sets or canonical id formats. Rejected values become `null` plus a fixed state and are never serialized. Tests put sentinels inside allowed fields and use objects with custom `__str__`/`__repr__`/`__eq__`.
- **Q2 (status, readiness, hint).**
  - Status origins are per branch:
    - `aiohttp_final_response_status`, with a separate `redirects_followed` count.
    - `websockets_library_contract_101`, only for `direct` connections when native discovery is cached as absent.
    - `aiohttp_routed_upgrade_same_scope`, only when a routed upgrade hop with status 101 was traced in the same scope.
    - Otherwise `null` with an `unknown_*` origin.
  - `capture_open` is defined as a startup sink-open marker.
  - The actual opener hint is not observed at the allowed bridge send seam. A cancelled creator may open the connection before a different request sends its first frame. The bridge hint therefore remains `not_observed`; it is never inferred from the first sender's request state. A directly traced HTTP hop records only its own validated routing-hint tier.
- **Q3 (identity and joins).**
  - The request-log id is named `request_log_response_id`, and its origin is always `request_log_row_request_id_caller_dependent`.
  - The bridge upstream id comes from the bridge request state at detach, with `replay_alias_present`.
  - The send receiver is snapshotted before the await. Connection references are monotonic integers held in a weak-key registry, never `id()`.
  - Bridge tier notes are not consumed by the first send. They apply only to attempt ordinal 1. Retries record `not_observed`.
  - Raw-websocket direct frames have only a `pre_send` record, which the classifier treats as `UNKNOWN`.
- **Q4 (handoff tests).** The core-client trace test (T7) covers only the archive and trace seams. Actual-tier handoff is covered by:
  - running the real `ProxyService._stream_once` (non-bridge) into the real `_write_request_log` hook;
  - running the real held `_finalize_websocket_request_state` (bridge) into the hook, including a replay-alias id.
  - invalid and missing raw returned tiers through that finalizer. The service collapses these values to `None`, so the allowed capture seams label normalized `None` as `not_observed`, not proved upstream omission. The classifier withholds a verdict for an unresolved returned tier even when the outgoing tier is non-priority.

## Design changes during implementation

`app/modules/proxy/_service/http_bridge/mixin.py` is at 2435 of the 2436-line architecture ceiling, and the threshold lives in `openspec/specs/proxy-architecture/spec.md`, so the planned open/failure hooks there were not made. The bridge connection is observed at the send seam instead: route mode comes from the public `upstream_proxy_route_mode`, and native discovery is read from its cached result only. The numeric status of a rejected direct bridge handshake therefore remains PARTIAL.

The record bound is 2048 bytes rather than the 1024 bytes of the formal design. A full `request_log_row` with per-field state and origin labels exceeds 1024 bytes, and the capture correctly disabled itself in testing. The bound is still fixed, and every validated field has a bounded maximum length.

The sink pins its file identity (device, inode) when it opens. Before each write it checks that identity and refuses to write to or close a descriptor that has been closed and reused. A test showed that writing to a reused descriptor number can inject a record into an unrelated aiohttp socket.

The 2026-10-06 Manager Codex completion step also checks descriptor identity on every failure-close path, including record-limit failure before a write. The weak connection registry has the same tracking bound as the other registries; when full, new connections remain untracked and their observations are PARTIAL while existing references remain stable. The classifier requires one normal row, matching canonical send/settlement identities and explicit local-send completion. An unresolved upstream response id blocks the tier verdict, including on the direct HTTP path; the independently observed tiers and status remain available as partial evidence. These corrections preserve the original interrupted producer's UNKNOWN history and do not certify runtime behavior.

## Classification rules (`classify_capture`)

1. The conversation id must be a canonical UUID, and a `capture_open` record must exist.
2. Choose the bridge or direct path as follows:
   - **Bridge:** exactly one `bridge_request_settled` with that conversation and `request_kind == "normal"`.
   - **Direct:** otherwise, exactly one normal `request_log_row` for the conversation, joined to `outgoing_payload` and `upstream_http_hop` by ingress id or scope id.
3. **Bridge completeness requires all of the following:**
   - exactly one normal request-log row, `attempt_count == 1`, and one local-completed send record of ordinal 1 for the same canonical ref, ingress and conversation;
   - `pending_request_count == 1` at send;
   - only one captured send on that connection; multiple sends, including another conversation or sequential reuse, remain PARTIAL because these seams do not expose an explicit frame-to-response mapping;
   - an outgoing tier in state `present` or `absent`;
   - a known handshake status for the connection, either on the send itself or on the first send of the same connection reference;
   - `replay_alias_present` false;
   - an upstream response id present;
   - status `success`.
4. **Direct completeness requires all of the following:**
   - exactly one HTTP `pre_send` payload record and one `POST` hop with a response;
   - no websocket `pre_send` record, because a raw-websocket frame send is unproven;
   - status `success`.

   The upstream response id is always reported unresolved, so the direct-path verdict remains `UNKNOWN`/`PARTIAL` even when the separate outgoing tier, returned tier and HTTP status observations are available.
5. The tier verdict comes from the at-origin outgoing tier (`priority` or `fast` means priority intent) and the returned tier. Any failed prerequisite yields `UNKNOWN` with reason codes and completeness `PARTIAL`.

## Readiness and adoption

The integration owner starts a fresh isolated run with the file path in a private run directory. Before the one labelled request is issued, `capture_open` must exist, and its process id and app version must match the adopted build and the guard's child process. Absence is diagnosed (unsupported config, rejected path, capture failure) before any request is sent. Local send completion never proves provider acknowledgement, honouring or billing.

## Example record

```json
{"schema":"codex-lb-wire-capture/1","seq":7,"at_unix":1786000000.123,"record":"bridge_frame_sent","send_state":"local_send_completed","ingress_request_id":"4c1a2f0e-7d39-4e5b-9a61-0f3b2c8d9e10","ingress_request_id_state":"present","scope_id":"8f9e0d1c-2b3a-4c5d-8e7f-a0b1c2d3e4f5","scope_id_state":"present","bridge_request_ref":"ws_0123456789abcdef0123456789abcdef","bridge_request_ref_state":"present","conversation_id":"01a10e4d-1104-7bc2-97b4-a7de1c6159cd","conversation_id_state":"present","attempt_ordinal":1,"pending_request_count":1,"connection_ref":1,"connection_ref_state":"registered","connection_first_send":true,"route_mode":"direct","route_mode_state":"present","handshake_status":101,"handshake_status_origin":"websockets_library_contract_101","opener_hint_tier":null,"opener_hint_tier_state":"not_observed","opener_hint_tier_origin":null,"outgoing_service_tier":"priority","outgoing_service_tier_state":"present","outgoing_service_tier_origin":"prepared_response_create_dict_field_first_send"}
```
