## Why

Upgraded Codex clients send `include: ["output.encrypted_content"]` on ordinary Responses requests, including remote compaction. The Responses include allowlist did not contain that value, so `/backend-api/codex/responses` and `/v1/responses` answered each such request with the generic 400 `include` envelope before routing.

## What Changes

- Add `output.encrypted_content` to the Responses `include` allowlist so it is accepted and forwarded upstream unchanged, alone or beside `reasoning.encrypted_content`.
- Keep rejecting every value outside the allowlist with the existing 400 `include` envelope, including when an allowlisted value shares the array.
- Pin acceptance and rejection in the request model and on both canonical HTTP Responses routes, with forwarding observed at a stubbed upstream.

## Impact

One allowlist entry in `app/core/openai/requests.py`, its tests, this capability's requirement and its reference include list. No setting, schema, routing, retry, compaction sizing, replay normalization or upstream transport change. The separately delivered runtime overlay already carries this entry; this change keeps maintained source in step so future builds retain it.
