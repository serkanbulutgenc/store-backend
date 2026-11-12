---
title: Frontend: Dashboard import screen
repo: store-frontend
slice: 11
type: AFK
status: closed
assignee:
blocked-by: [39-be-import.md]
---

## Scope

- Owner-only screen: choose a CSV, see the preview (counts, row errors in a table, what will be created), confirm, then see the result.
- Nav entry under Katalog (Ürünler group once the shell lands); download link for the template CSV.
- Update `store-infra/docs/launch/stock-load.md` so the import is the stock-load method.

## Acceptance

- [x] A preview with errors blocks the confirm button; a clean preview confirms and shows the created and updated counts.
- [x] Staff do not see the screen or the nav entry.

## Spec

- [Phase 2 section 2](../../spec/phase-2.md)
