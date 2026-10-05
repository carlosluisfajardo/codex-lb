# Merge reviewed Fast and account metadata delivery

## Why

The independently reviewed Fast and account lifecycle candidates share the
v1.25.0-beta.8 parent. Their frozen additive migrations occupy the same timestamp
slot and currently produce two Alembic heads. Installation must preserve those
reviewed revision identities while providing one coherent graph and package.

## What Changes

- Add a no-op merge revision joining the two unchanged frozen revisions.
- Permit only this exact frozen sibling pair when its named merge is present;
  keep prospective timestamp collisions and malformed graphs rejected.
- Qualify the combined upgrade, candidate-aware rollback and re-upgrade on an
  isolated persistent database, preserving existing account and settings data.
- Ship the complete account frontend with both backend features. Fast stays off
  until separately enabled through a fresh settings compare-and-set.

## Impact

No Phase2 reset or schedule work is included. Installation, original-owner
service adoption and real-user acceptance remain separate delivery steps.
