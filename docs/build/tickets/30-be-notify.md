---
title: Backend: wire Shopper emails, staff notifications and timed jobs
repo: store-backend
slice: 8
type: AFK
status: closed
assignee:
blocked-by: [29-be-outbox.md, 26-be-orders.md, 20-be-settings.md]
---

## Scope

- Templates and triggers: order placed (bank and COD variants), 24 h reminder, auto-cancel, paid, shipped, cancelled, return/refund, verification, reset, email change; no "delivered" mail.
- Staff: email per new order to the configured recipients; daily low-stock digest; Owner email on deletion requests.
- Celery beat: 24 h reminder and 48 h auto-cancel with stock release.

## Acceptance

- [ ] A bank transfer order not paid in 48 h is cancelled and its stock released (time-travel test).
- [ ] Order email shows IBAN, holder and number only; link expires in 30 days; footer carries seller details.

## Spec

- [PRD section 2](../../spec/prd.md)
- [PRD section 3](../../spec/prd.md)
