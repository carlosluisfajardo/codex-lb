# Omit the unsupported Force Probe output limit

The maintained fork sends `max_output_tokens=16` in Force Probe requests. Upstream
fix #2496 removes this unsupported Responses parameter. A rejection prevents the
probe from testing account recovery even though its existing forced usage refresh
and advisory settlement still run.

Remove only that request parameter and its obsolete constant. Preserve account
pinning, the minimal streamed request, forced refresh, selection-cache invalidation,
and settlement based on fetched usage evidence. Prove the correction through the
real dashboard route with synthetic account material and stubbed external I/O.

No live probe, account operation, installation, or LB replacement is part of this
source change.
