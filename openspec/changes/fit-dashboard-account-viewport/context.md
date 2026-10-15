The dashboard remains a dense operational overview with the same account card
content and actions. Measuring actual grid rows avoids replacing one stale fixed
height with another. Offset geometry ignores the existing fade-in transforms;
observing the grid and its children covers responsive columns and asynchronously
loaded lifecycle notes. Native scrollbars follow the operator's OS preference.

For example, a 411px first row and a 356px second row with a 16px gap require a
783px viewport, rather than the previous 384px cap. Later rows remain internally
scrollable. The list keeps its 36rem cap but exposes both scroll axes on its outer
viewport, so a wide date column cannot hide the vertical scrollbar offscreen.

Rollback is a source revert of this change; no data or configuration changes.

2026-10-15 — Author: Sol producer, bounded LB5 viewport follow-up from merged
`21b35890`; focused unit/integration, type/lint and OpenSpec checks pass, and
independent Astra source challenge is CONFIRMED. The maintained browser smoke
reproduced first-row action and second-row clipping on the old build (8 passed,
1 failed), then passed all 9 checks on the correction and again on the final
uninstrumented test. Actual rows measured 428px and 356px, with an 800px corrected
viewport instead of 384px. Before/after screenshots are in `evidence/`.
All ephemeral QA processes and data are released. Source PR and original-session
Opus follow-up remain pending.
