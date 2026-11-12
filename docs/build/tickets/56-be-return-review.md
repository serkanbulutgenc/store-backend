---
title: Backend: Staff approve or reject returns, emails and restocking
repo: store-backend
slice: 14
type: AFK
status: closed
assignee:
blocked-by: [55-be-return-requests.md]
---

## Scope

- Approve or reject (rejection needs a reason), mark received (per-line restock choice), then the existing refund recording; audit entries for each step.
- Emails through the outbox: request received (Shopper and Staff), approved or rejected, items received, refund sent; the approval email says who pays return shipping (shop for defective and wrong item sent).

## Acceptance

- [x] Each step is audited and sends exactly one email.
- [x] Marking received with restock adds the units back through the stock module.

## Spec

- [Phase 2 section 5](../../spec/phase-2.md)

## Decision (Order status after returns)

A request moving through approved, received and refunded never changes `Order.status`, even when every unit comes back; whole-Order `returned` stays Staff's phase 1 action. A refund on a request is capped by what was paid minus what was already refunded, so several requests on one Order can each be refunded. Restocking is chosen per line when marking received (`ReturnLine.restocked`). Audit actions: `return_approved`, `return_rejected`, `return_received`, `refund`; Dashboard return-request screens (ticket 58) read `returns` on the admin Order detail.
