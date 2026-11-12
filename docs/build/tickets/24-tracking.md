---
title: Order tracking: signed link, endpoint and page
repo: store-backend
slice: 5
type: AFK
status: closed
assignee:
blocked-by: [22-be-checkout.md, 23-fe-checkout.md]
---

## Scope

- Signed order link valid 30 days; rate-limited tracking endpoint by Order number plus email or phone; `/siparis-takip` page in the frontend showing status, items, totals, tracking number. No phone or full address in URLs.

## Acceptance

- [ ] The link opens one Order and expires after 30 days; tracking by number plus email still works afterwards.
- [ ] Repeated wrong lookups are rate-limited.

## Spec

- [PRD section 2](../../spec/prd.md)
