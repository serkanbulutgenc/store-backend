---
title: Invoice record, invoice sheet and Home flags
repo: store-backend
slice: 9
type: AFK
status: closed
assignee:
blocked-by: [26-be-orders.md, 27-fe-admin-orders.md]
---

## Scope

- Invoice record per Order (number, date, status not issued/issued/cancelled, credit-note number and date), set by hand; invoice sheet view with the fields the portal needs; 8-day cancellation reminder.
- Flags and Dashboard Home lists: shipped without an invoice; invoiced Orders that were cancelled or returned and need a credit note; plus the other Home lists (new Orders, unpaid transfers near expiry, low stock, published Products without photos). Needs API plus Dashboard UI.

## Acceptance

- [ ] Marking shipped surfaces the Order in "shipped without an invoice".
- [ ] Cancelling an invoiced Order raises the credit-note flag until recorded.

## Spec

- [PRD section 4](../../spec/prd.md)
- [PRD section 6](../../spec/prd.md)
