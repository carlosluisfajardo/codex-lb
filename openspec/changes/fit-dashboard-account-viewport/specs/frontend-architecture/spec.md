## RENAMED Requirements

- FROM: `### Requirement: Account card row height is 11.5rem`
- TO: `### Requirement: Dashboard account viewports fit their content and remain scrollable`

## MODIFIED Requirements

### Requirement: Dashboard account viewports fit their content and remain scrollable

The dashboard account card viewport MUST fit two complete rendered grid rows,
including their gap, instead of assuming a fixed per-row height. It MUST update
its bound when card content or the responsive column layout changes. Additional
rows MUST remain reachable by native scrolling and keyboard navigation. The card
and list viewports MUST be named, keyboard-focusable scroll regions and MUST NOT
hide native scrollbars. The list MUST use one outer viewport for both scroll axes
while retaining its existing height cap and sticky headers.

#### Scenario: Account card max height

- **WHEN** at least seven accounts render at 1440px with long datetime and timezone
  notes in a first-row card
- **THEN** every first-row action fits inside the card scroll viewport
- **AND** the second row is fully inside that viewport
- **AND** the third row remains reachable by scrolling

#### Scenario: Content and responsive changes update the bound

- **WHEN** lifecycle notes load, font metrics change, or the grid changes columns
- **THEN** the viewport is remeasured from the first two rendered rows
- **AND** fade-in transforms do not change which cards belong to each row

#### Scenario: Keyboard users reach later accounts

- **WHEN** an operator focuses an account viewport and tabs to a later account's action
- **THEN** focus reaches the action and the viewport scrolls to expose it
- **AND** list actions remain reachable without first moving to a separate inner scroller
