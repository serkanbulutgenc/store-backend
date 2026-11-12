---
title: Backend: store and admin Ninja APIs, envelope, pagination, spec export
repo: store-backend
slice: 2
type: AFK
status: closed
assignee: claude
blocked-by: [05-be-oscar.md]
---

## Scope

- Two `NinjaAPI` instances at `/api/store/` and `/api/admin/` with thin handlers and a `services/` package.
- Global exception handler producing `{code, message, fields}` with Turkish messages and the agreed statuses; offset pagination `{items,total,page,page_size}` (max 100); `ordering` allowlist; `FilterSchema` plus `q`.
- Management command exporting `openapi-store.json` and `openapi-admin.json` with explicit short operationIds; CI step failing when the committed specs are stale.
- Money as decimal strings.

## Acceptance

- [x] Each API serves its own OpenAPI document.
- [x] A 409 and a validation error render the envelope (tests).
- [x] CI fails when a spec drifts from the code.

## Spec

- [Architecture section 2](../../spec/architecture.md)
