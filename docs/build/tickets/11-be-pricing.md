---
title: Backend: KDV-inclusive pricing strategy and line snapshots
repo: store-backend
slice: 3
type: AFK
status: closed
assignee:
blocked-by: [10-be-catalog.md]
---

## Scope

- Oscar pricing strategy returning the KDV-inclusive shelf price with the rate from the Product class; derived exclusive and KDV amounts.
- Line-snapshot calculator: per-line rounding to kuruş, order totals as the sum of lines; shipping and COD lines with a configurable rate.

## Acceptance

- [x] Build check 5: shelf price, derived exclusive amount and KDV amount round-trip with no kuruş drift across a table of prices (tests).
- [x] Order totals equal the sum of rounded lines.

## Spec

- [Architecture section 2](../../spec/architecture.md)
- [Architecture section 6](../../spec/architecture.md)
