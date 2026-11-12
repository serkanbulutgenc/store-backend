---
title: Backend: bulk catalogue import service and endpoints
repo: store-backend
slice: 11
type: AFK
status: closed
assignee:
blocked-by: [38-be-catalog-tidy.md]
---

## Scope

- Owner-only dry-run endpoint taking a CSV and returning the preview: rows to create, rows to update, per-row errors, and the brands, suppliers and categories that would be created. Writes nothing.
- Confirm endpoint applying the same file in one transaction (all rows or none): Products created as drafts, rows with the same product name and category become variants of one Product, match existing variants by `sku` else product name + colour + size, stock through `stock.adjust` so it is audited.
- Settle the final column set: add `product_class` and an optional `sku`, clarify `kdv_class`; update `docs/launch/stock-template.csv`. Unknown colours, sizes outside the class scheme and bad prices are row errors; missing brands, suppliers and categories are created on confirm.
- One audit entry per run (who, file name, counts).

## Acceptance

- [x] A file with one bad row changes nothing on confirm and names the row and column.
- [x] Re-uploading a corrected file updates prices and stock instead of duplicating variants (tests).
- [x] Staff cannot call either endpoint (403); a 1,000-row file previews in a few seconds.

## Spec

- [Phase 2 section 2](../../spec/phase-2.md)
