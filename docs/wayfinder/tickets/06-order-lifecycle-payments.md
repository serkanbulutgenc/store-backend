---
title: Grill: order lifecycle, payment methods and stock handling
label: wayfinder:grilling
status: closed
assignee: claude (serkanbulutgenc@gmail.com session)
blocked-by: [01-research-oscar-fit.md, 04-research-turkey-commerce-rules.md]
parent: map.md
---

## Question

Decide the order state machine for bank transfer and cash on delivery: states and transitions, who confirms payment and how, bank-transfer deadline/auto-cancel, stock reservation/allocation timing, cancellation rules, what staff can do at each state. Update CONTEXT.md.

## Resolution

Decided with the user (all recommended answers accepted):

1. **Two independent statuses.** Fulfilment status: `new → preparing → shipped → delivered → completed`, plus `cancelled`, plus return states `return_requested → returned`. Payment status: `unpaid → paid → refunded`. Oscar's order pipeline carries fulfilment; payment status derives from Oscar payment records (Source/Transaction).
2. **Bank transfer.** Order placed as `new`/`unpaid` with stock reserved. 48 h to pay, reminder at 24 h, then auto-cancel and release stock. Each order has a short unique order number the shopper writes in the transfer description. No bank API in phase 1: staff checks the statement and presses "mark as paid" (amount and date recorded). Wrong/partial amounts handled by hand.
3. **Cash on delivery.** Straight to `preparing` (no confirmation call). Phone number mandatory for every order. COD fee is a configurable fixed amount, default 0, shown as an explicit line the shopper consents to. No value cap in phase 1. Recorded `paid` when staff marks the order delivered.
4. **Stock.** Reserve at placement for both methods (Oscar default), release on cancel/auto-cancel, consume on shipping. Physical-shop sales adjust stock manually in the dashboard (POS sync stays in the fog). If an order cannot be fulfilled, staff cancels with a reason, triggering the legal 3-day notice and 14-day refund. Dashboard flags low stock.
5. **Cancellation and returns.** Staff-only cancel before shipping (reason recorded); shoppers ask by contacting the shop. After delivery staff moves the order `return_requested → returned`, then payment `refunded`, recording amount and refund method/IBAN. No self-service, no partial returns, no automation in phase 1. Items can be flagged non-returnable (opened underwear/hygiene exception).
6. **Shipping.** One flat shipping fee with a free-shipping threshold, both configurable. Staff enters carrier name and tracking number by hand when marking shipped. No per-city or weight rates.

Legal constraints this design must respect (from research 04): refund to the original payment instrument within 14 days, delivery within 30 days, out-of-stock is not an excuse for non-delivery, COD fee needs separate consent, keep withdrawal/information/delivery records 3 years. Lawyer should confirm bank-transfer refund handling and the 30-day clock for unpaid transfers.
