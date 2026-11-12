---
title: Frontend: Shopper cancel and return request pages
repo: store-frontend
slice: 14
type: AFK
status: closed
assignee:
blocked-by: [55-be-return-requests.md]
---

## Scope

- Cancel button and a return form (lines, quantities, reason) on the tracking page and in the account order detail; shows request status and the return-shipping payer.

## Acceptance

- [x] Hygiene lines cannot be ticked; the form hides after day 14 with an explanation.
- [x] Works for guests via the signed link and for signed-in customers.

## Spec

- [Phase 2 section 5](../../spec/phase-2.md)
