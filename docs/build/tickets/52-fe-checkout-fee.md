---
title: Frontend: checkout shows the computed shipping fee
repo: store-frontend
slice: 13
type: AFK
status: closed
assignee:
blocked-by: [50-be-checkout-fees.md]
---

## Scope

- Checkout recomputes the shipping line when the province changes; basket shows an estimate or a note that shipping is calculated at checkout.

## Acceptance

- [ ] Changing the province changes the fee and total shown before the order is placed; the placed order matches the quote.

## Spec

- [Phase 2 section 4](../../spec/phase-2.md)
