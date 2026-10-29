# Scope and evidence

The Python parser waits separately for each body chunk; SSE event completion is
not required to renew receive idle. The request-level generic timeout classifier
must observe the same activity. Native framed responses retain their own
transport deadlines and error types. A small parser-only response adapter carries
the activity callback for Python bodies without changing response ownership or
the parser's three-argument interface.

Example: bytes at 300, 600, ... 6900 seconds keep receive idle valid, while the
original 7200-second total expires before an SSE separator. That is total expiry,
not a 7200-second idle gap. By contrast, a fragment followed by 600 seconds of
silence remains an idle timeout. Existing genuine-idle/total ties are preserved.

Hermetic source tests establish the source behavior only. They do not establish
an installed proxy's behavior, an operator incident cause or live account state.
