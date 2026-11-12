---
title: Frontend: Dashboard Audit log screen
repo: store-frontend
slice: 12
type: AFK
status: closed
assignee:
blocked-by: [42-fe-shell.md, 45-be-customers-audit.md]
---

## Scope

- Owner-only list with filters (actor, action, date range) and before/after detail.

## Acceptance

- [x] `/admin/audit-log` renders for the Owner only; Staff do not see the nav item and a direct visit is refused.

## Spec

- [Phase 2 section 3](../../spec/phase-2.md)
