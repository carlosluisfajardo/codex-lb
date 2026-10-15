# Browser evidence

Captured in headless Chromium on 2026-10-10 from two locally built frontend bundles: before from
the delivery base `1da2c912`, after from this change. Both builds received the same fixture API
responses through Playwright routes, with the clock fixed at `2026-10-10T12:00:00Z`, the browser
timezone set to UTC and reduced motion on. No backend, account, credential or provider was
involved, and no lifecycle write was sent.

Fixture accounts:

- **Renewing account**: `Renews on` is the civil date `2026-10-31`, `Ends on` is unset. It shows
  `in 21 days`.
- **Lapsed account with a long operator label**: `Ends on` is `2026-10-12 09:30:15
  America/Argentina/ComodRivadavia` (`in 2 days`, due soon). `Renews on` is `2026-09-28`, so it
  passed 12 days ago and needs confirmation.
- **Due soon account** (rate limited): `Ends on` is today. `Renews on` is `2026-10-15 08:00
  +09:00` (`in 5 days`).
- **Unread account**: the lifecycle read fails, so its dates show as unavailable.

The summary reads `Subscription dates: 1 passed · 1 due today · 1 unknown`. Each account is
counted once, and the existing `registered · active · unavailable` counts are unchanged.

| View | Before | After |
| --- | --- | --- |
| Cards, 1440×900 | `before-cards-1440.png` | `after-cards-1440.png` |
| List, 1440×900 | `before-list-1440.png` | `after-list-1440.png` |
| Cards, 390×844 | `before-cards-390.png` | `after-cards-390.png` |
| List, 390×844 | `before-list-390.png` | `after-list-390.png` |

Measured on both builds:

- The document does not scroll horizontally at 320, 390 or 1440 pixels.
- At 1440 pixels the list fits without a horizontal scrollbar (1390 of 1390 pixels).
- A list row with date-only values keeps the 72-pixel baseline height. The long-timezone row wraps
  inside its cell to 172 pixels.
- A typical card grows from 308 to 356 pixels, which still fits the card viewport's first row.
- On the base build the card grid was already wider than the viewport at 320 and 390 pixels
  (392-pixel cards) and scrolls inside its own container. This change leaves that unchanged.

The permanent regression is the `subscription dates` browser smoke test in
`frontend/browser-smoke/dashboard.spec.ts`. It covers cards and list at 320, 390 and 1440
pixels, including long timezone containment.
