---
title: Backend: admin customers list/detail and audit-log list endpoints
repo: store-backend
slice: 12
type: AFK
status: closed
assignee:
blocked-by: []
---

## Scope

- Read-only admin endpoints: customers (search by name or email, order count, detail with their orders) behind `view_customers`; audit-log list (filter by actor, action, date; paginated) behind `view_audit_log`, Owner-only.
- Standard envelope and pagination; regenerate `openapi-admin.json`.

## Acceptance

- [x] Staff without the permission get 403; the audit list never exposes more than the existing order-detail audit does; before/after are only on the Owner-only single-entry endpoint (added for ticket 47).
- [x] Anonymised customers show as anonymised.

## Spec

- [Phase 2 section 3](../../spec/phase-2.md)
