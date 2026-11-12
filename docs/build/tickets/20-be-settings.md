---
title: Backend: Store settings and Legal page versions
repo: store-backend
slice: 5
type: AFK
status: closed
assignee:
blocked-by: [08-be-roles.md]
---

## Scope

- Singleton Store settings: shipping fee and threshold, COD fee, KDV rate for shipping and COD lines, low-stock default, bank details, notification recipients, homepage banner, seller identity.
- Owner-only read and write endpoints, audit-logged; public read of the safe subset for the storefront.
- Legal page version identifiers exposed so checkout can record them.

## Acceptance

- [ ] Staff cannot change settings (403).
- [ ] Changing a setting writes an audit entry.

## Spec

- [PRD section 2](../../spec/prd.md)
- [PRD section 4](../../spec/prd.md)
