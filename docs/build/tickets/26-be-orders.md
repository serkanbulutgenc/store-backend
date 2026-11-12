---
title: Backend: admin order operations
repo: store-backend
slice: 7
type: AFK
status: closed
assignee:
blocked-by: [22-be-checkout.md, 08-be-roles.md]
---

## Scope

- Transitions: mark paid (amount and date), cancel with reason (paid cancellation Owner-only), ship with carrier and tracking, deliver, return requested/returned, refund (Owner-only, amount and method/IBAN); COD becomes paid on delivery.
- Edits: shipping address and phone only while new or preparing; email correction (triggers resend); buyer tax number and company title; all audited.
- Stock released on cancel and consumed on shipping.

## Acceptance

- [ ] Illegal transitions return 409.
- [ ] Every transition writes an audit entry and releases or consumes stock as specified (tests).

## Spec

- [PRD section 3](../../spec/prd.md)
- [PRD section 4](../../spec/prd.md)
