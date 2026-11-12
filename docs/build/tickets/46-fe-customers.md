---
title: Frontend: Dashboard Customers screen
repo: store-frontend
slice: 12
type: AFK
status: closed
assignee:
blocked-by: [42-fe-shell.md, 45-be-customers-audit.md]
---

## Scope

- Read-only list with search and a detail page (profile, orders); uses the shared DataTable.

## Acceptance

- [x] `/admin/customers` no longer hits the not-found page; Staff without `view_customers` do not see it.

## Spec

- [Phase 2 section 3](../../spec/phase-2.md)
