---
title: Frontend: basket, checkout and confirmation pages, legal pages
repo: store-frontend
slice: 5
type: AFK
status: closed
assignee:
blocked-by: [18-fe-browse.md, 22-be-checkout.md]
---

## Scope

- Basket page; one-page checkout in a single TanStack Form with a Zod schema; summary with items, shipping, COD fee, total and withdrawal summary; obligation-stating confirm button; separate privacy notice line and unticked consent boxes; email typo hint.
- Static versioned Markdown Legal pages (placeholder text until the lawyer supplies it) and footer links; confirmation page with bank details and the create-account offer.

## Acceptance

- [ ] A guest can place a bank transfer and a COD order end to end against a dev backend.
- [ ] A 409 shows inline and refreshes the basket.

## Spec

- [PRD section 2](../../spec/prd.md)
