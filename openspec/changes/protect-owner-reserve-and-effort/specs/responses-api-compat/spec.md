## ADDED Requirements

### Requirement: Catalog-scoped ultra effort floor

After model aliases and API-key enforcement, Responses and compact requests whose resolved model advertises `ultra` in the catalog snapshot SHALL use at least `ultra`, serialized by the existing wire alias as `max`. Requests for other models or without a catalog snapshot SHALL retain existing effort handling. The floor SHALL preserve model, service tier, and reasoning summary.

#### Scenario: Lower enforced effort is raised
- **GIVEN** an API key enforces low effort on a model advertising ultra
- **WHEN** request policy normalizes the request
- **THEN** the upstream effort is max
- **AND** the service tier is unchanged
