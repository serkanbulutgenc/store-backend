---
title: Backend: basket API
repo: store-backend
slice: 5
type: AFK
status: closed
assignee:
blocked-by: [07-be-auth.md, 10-be-catalog.md, 11-be-pricing.md]
---

## Scope

- Store basket endpoints over Oscar baskets (add, change quantity, remove, read with totals); signed cookie, 14 days; guests need CSRF; baskets never reserve stock; stock and sold-out validation on add.

## Acceptance

- [ ] Basket survives a new session via the cookie and merges on login (covered by the be-auth check).
- [ ] Quantity above stock returns a 409 envelope.

## Spec

- [PRD section 2](../../spec/prd.md)
