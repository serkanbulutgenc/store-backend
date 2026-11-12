---
title: Backend: Shopper cancel and return requests
repo: store-backend
slice: 14
type: AFK
status: closed
assignee:
blocked-by: [54-be-outbox-seam.md]
---

## Scope

- Shopper cancel (order new or preparing) through the signed tracking link and for signed-in customers; stock released through `stock.release`.
- ReturnRequest and ReturnLine records with their own status (requested, approved, rejected, received, refunded): 14 days from delivery, selected lines and quantities, hygiene lines refused, reasons (wrong size, changed mind, defective, wrong item sent, other).
- Reconcile with the phase 1 Order-level return statuses and the `RULES` table in `order_ops.py` so a partial return does not mark the whole Order returned; record the decision in the ticket.

## Acceptance

- [ ] A request after day 14, for a hygiene line, or for more than was delivered is refused with a clear error (tests).
- [ ] A partial return leaves the Order status delivered and the request visible on it.

## Spec

- [Phase 2 section 5](../../spec/phase-2.md)

## Decision (reconciliation with phase 1 return statuses)

Shopper return requests are separate `ReturnRequest`/`ReturnLine` records and never change `Order.status`. The Order-level `return_requested` and `returned` statuses and the `request_return`/`mark_returned` rows in `RULES` stay as the Staff's whole-Order flow, untouched. A partial return therefore leaves the Order `delivered` with the request listed on it. Ticket 56 moves a request through approved, received and refunded, and decides when (if ever) a fully returned Order becomes `returned`.
