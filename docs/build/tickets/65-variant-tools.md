---
title: Variant matrix generator and bulk stock grid
repo: store-backend + store-frontend
slice: 16
type: AFK
status: open
assignee:
blocked-by: []
---

## Scope

- The Variant model stays colour × size. No new axes, no per-Variant photos or desi.
- Matrix generator: on a Product, pick colours (from the active list) and sizes (from the class's scheme), one shelf price (optional override) and an initial stock; create every missing combination in one transaction, skip existing ones, report created and skipped. SKUs auto-generate as today.
- Bulk stock grid: one grid per Product (colour rows × size columns, only existing Variants editable). Saving applies the changed cells through `stock.adjust`, so every change is audited with the usual reason; unchanged cells write nothing.
- Both are open to Staff. No copying Variants between Products and no bulk price override.
- Dashboard: both live in the product editor's Variants tab.

## Acceptance

- [ ] Choosing 3 colours × 4 sizes creates 12 Variants; running it again creates none and says all were skipped.
- [ ] A size outside the class scheme or an inactive colour is refused.
- [ ] Editing three cells in the grid writes exactly three audited stock adjustments.
- [ ] A Staff user can use both; stock cannot go negative.

## Spec

- CONTEXT.md terms Variant, SKU, Size scheme; phase-2.md bulk import (same SKU rules).
