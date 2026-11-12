---
title: Backend: admin catalog API and stock adjustments
repo: store-backend
slice: 3
type: AFK
status: closed
assignee:
blocked-by: [10-be-catalog.md, 08-be-roles.md]
---

## Scope

- Admin endpoints for Products with Variants, Categories, Brands, Suppliers, Colour list with permissions (Colour list, Supplier data, cost price, archive Owner-only).
- Stock adjustment service with reason, audit-logged; low-stock list endpoint.
- Publishing requires at least one photo once the media ticket lands (hook now, enforce there).

## Acceptance

- [x] Staff cannot see cost price or Supplier data (response omits fields).
- [x] A stock adjustment without a reason is rejected; every adjustment appears in the audit log.

## Spec

- [PRD section 4](../../spec/prd.md)
- [PRD section 5](../../spec/prd.md)
