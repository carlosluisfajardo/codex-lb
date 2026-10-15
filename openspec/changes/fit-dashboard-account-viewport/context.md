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

2026-10-15 — N1 follow-up author: assigned Astra source producer, fresh derivative
from merged `0f6abee3`. The retained Opus re-check closes L1 and introduces LOW N1;
it ran nothing, and its laptop dimensions are estimates. Choose its one-column
page-flow alternative: when the first two cards occupy different rendered rows,
remove the internal vertical cap. For example, the unit fixture's 520px/460px
rows plus their 16px gap switch from a 996px cap to no cap. The intended browser
behavior is page flow on a 568px-tall phone: the region may exceed the window
height without internal vertical overflow. Multi-column layouts retain the
measured two-row behavior, native scrollbars and keyboard access from L1. This
does not claim a viewport clamp for short multi-column windows or invent a fixed
header/status allowance. The existing list and its 36rem cap are unchanged.

2026-10-15 — N1 publication update: assigned Astra source publication owner.
The fresh non-author Sol source challenge is CONFIRMED with no material findings.
The source author's unchanged unit pin failed on the original component
(expected none, received 996px; 1 failed, 6 passed) and passed on the correction
(7 passed). The assigned N1 QA worker then witnessed the real browser assertion
fail on the exact old component and pass on the corrected component, using the
same synthetic subscription-dates test with capture instrumentation in both runs.
The old 320×568 region had clientHeight 832px and scrollHeight 2690px, failing
the no-internal-overflow assertion. Corrected 320×568 and 390×844 regions measured
2690px/2690px and passed page-wheel and last-action keyboard checks. At 1440×900
and 1440×1200, the existing measured two-row cap was 800px with 1171px of content;
native-scrollbar and keyboard controls passed. The list retained its 36rem cap.
Sequential baseline/corrected type-and-build commands passed. The QA runner
terminated successfully and released its synthetic fixture processes and data.

Distinct N1 evidence below preserves the exact captured PNG bytes. Both images
show the same initial 320×568 window, before interaction; the cap extends below
that window in both versions, so the images alone do not reveal the overflow
difference. The measured geometry and unchanged red/green assertions establish
the correction. These are synthetic accounts, not a live runtime capture.

- Before: [old component, 320×568](evidence/n1-before-320x568.png).
- After: [corrected component, 320×568](evidence/n1-after-320x568.png).

No short multi-column laptop viewport clamp is delivered or claimed. Original L1
evidence remains historical. Source publication and natural CI are tracked in the
PR; the outside operator's same original LB5-session N1 follow-up remains pending.
This evidence does not establish original-session Opus closure, installation or
live runtime acceptance.
