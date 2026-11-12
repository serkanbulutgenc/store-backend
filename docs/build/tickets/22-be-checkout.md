---
title: Backend: checkout and order placement service
repo: store-backend
slice: 5
type: AFK
status: closed
assignee:
blocked-by: [21-be-basket.md, 20-be-settings.md]
---

## Scope

- Checkout endpoint: guest or customer, mandatory fields, billing = shipping unless set, bank transfer or COD, COD fee consent, contract acknowledgement required, marketing consent optional with timestamp.
- One transaction: create the Order via `OrderCreator`, reserve stock, assign the short Order number, store per-line KDV snapshots, Consent record (Legal page versions, time, IP, user agent) and the Pre-information snapshot.
- Bank transfer starts new/unpaid; COD starts preparing and requires a phone. Rate limits per IP plus a honeypot field; 409 when stock vanishes.

## Acceptance

- [ ] An order cannot be placed without the contract acknowledgement or, for COD, the fee consent (tests).
- [ ] Stock is reserved at placement; the snapshot text matches the Order totals.
- [ ] Two concurrent orders for the last unit: one succeeds, one gets 409.

## Spec

- [PRD section 2](../../spec/prd.md)
- [PRD section 3](../../spec/prd.md)
