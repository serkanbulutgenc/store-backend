---
title: Backend: checkout and orders use the computed shipping fee
repo: store-backend
slice: 13
type: AFK
status: closed
assignee:
blocked-by: [49-be-shipping.md]
---

## Scope

- `quote` uses `shipping.fee_for`; free-shipping threshold still wins; fall back to Settings `shipping_fee` when it returns None. The shipping fee snapshot keeps its KDV split.
- Admin and Shopper order views keep showing the fee as paid; ship still lets Staff pick one of the three carriers.

## Acceptance

- [ ] An order to Zone 4 with 7 desi is charged the cheapest Zone 4 6-10 band price; above the threshold it is free.
- [ ] Existing orders and their fee snapshots are untouched.

## Spec

- [Phase 2 section 4](../../spec/phase-2.md)
