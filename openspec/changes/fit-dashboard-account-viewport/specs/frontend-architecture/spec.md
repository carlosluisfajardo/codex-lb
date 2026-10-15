## RENAMED Requirements

- FROM: `### Requirement: Account card row height is 11.5rem`
- TO: `### Requirement: Dashboard account viewports fit their content and remain scrollable`

## MODIFIED Requirements

### Requirement: Dashboard account viewports fit their content and remain scrollable

In multi-column layouts, the dashboard account card viewport MUST fit two complete
rendered grid rows, including their gap, instead of assuming a fixed per-row height.
In a one-column layout, cards MUST flow in the page without an internal vertical
height cap or vertical overflow. The bound MUST update when card content or the
responsive column layout changes. Additional rows MUST remain reachable by native
scrolling and keyboard navigation, using page scrolling in the one-column layout.
The card and list regions MUST be named and keyboard-focusable and MUST NOT hide
native scrollbars. The list MUST use one outer viewport for both scroll axes while
retaining its existing height cap and sticky headers.

#### Scenario: Account card max height

- **WHEN** at least seven accounts render at 1440px with long datetime and timezone
  notes in a first-row card
- **THEN** every first-row action fits inside the card scroll viewport
- **AND** the second row is fully inside that viewport
- **AND** the third row remains reachable by scrolling

#### Scenario: Content and responsive changes update the bound

- **WHEN** lifecycle notes load, font metrics change, or the grid changes columns
- **THEN** multi-column bounds are remeasured from the first two rendered rows
- **AND** entering one column removes the vertical cap and returning to multiple
  columns restores the measured two-row cap
- **AND** fade-in transforms do not change which cards belong to each row

#### Scenario: Short one-column viewport uses page scrolling

- **WHEN** at least seven accounts with long datetime and timezone notes render at
  320×568 in one column
- **THEN** the card region has no internal vertical overflow, even when its page
  content is taller than the window
- **AND** wheel scrolling over a visible card moves the page without vertically
  scrolling the card region
- **AND** keyboard navigation reaches the last card's action within the window

#### Scenario: Keyboard users reach later accounts

- **WHEN** an operator focuses an account viewport and tabs to a later account's action
- **THEN** focus reaches the action and the page or account viewport scrolls to expose it
- **AND** list actions remain reachable without first moving to a separate inner scroller
