---
title: Frontend: Dashboard carrier rates screen and desi fields
repo: store-frontend
slice: 13
type: AFK
status: closed
assignee:
blocked-by: [49-be-shipping.md, 44-fe-product-tabs.md]
---

## Scope

- Owner-only screen to edit the rate table, zones and province mapping; desi field on the Product class form and the product editor's Fiyat tab.
- Needs backend admin endpoints for rates; add them to ticket 49's scope if missing when this starts.

## Acceptance

- [x] Changing a price changes the fee shown at checkout for new quotes.
- [x] Staff cannot open the screen.

## Spec

- [Phase 2 section 4](../../spec/phase-2.md)
