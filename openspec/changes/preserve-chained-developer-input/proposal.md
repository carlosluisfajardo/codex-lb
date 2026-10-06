# Preserve developer input across chained Responses

## Why
Responses normalization currently moves developer input messages into top-level instructions. The upstream contract described in Soju06/codex-lb #2563/#2569 carries prior input through previous_response_id, while prior instructions are not inherited. Moving developer context therefore removes it from that inherited input.

## What Changes
- Keep developer messages in input, in order and with their normalized content.
- Continue hoisting ordinary system messages and merging them after explicit instructions.
- Keep typed directives and Responses Lite additional_tools behavior intact.
- Add provider-free normalized-payload regressions and update affected local expectations.

## Impact
- Affected spec: responses-api-compat.
- Source: app/core/openai/requests.py; affected request/mapping unit tests.
- Author: Manager Codex-lb, one bounded owner step after the supported fresh Opus launch ended before admission with no eligible producer seat.
- Source evidence only; no provider inheritance, install, runtime, incident-cause or live recovery acceptance is claimed.
