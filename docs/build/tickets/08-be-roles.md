---
title: Backend: Owner and Staff groups, permission checks, /admin/me, audit log
repo: store-backend
slice: 2
type: AFK
status: closed
assignee: claude
blocked-by: [06-be-api.md, 07-be-auth.md]
---

## Scope

- Migration seeding the fixed Owner and Staff groups with named permissions; each admin endpoint declares one permission; 403 in the envelope.
- `GET /api/admin/me` returns the permission list; Owner-only fields are omitted for Staff.
- Append-only audit log model and service (actor, time, target, short before/after) used by later tickets.
- Staff invitation by email and deactivation (no deletion); allauth MFA TOTP required for Owner, optional for Staff.

## Acceptance

- [x] An endpoint without its permission returns 403 for Staff and 200 for Owner (tests).
- [x] Audit entries are written through the service and cannot be edited.
- [x] Owner login without 2FA enrolment is blocked.

## Spec

- [PRD section 4](../../spec/prd.md)
