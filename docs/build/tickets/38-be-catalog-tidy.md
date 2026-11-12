---
title: Backend: catalog API view-models and owner-only policy into services
repo: store-backend
slice: 11
type: AFK
status: closed
assignee:
blocked-by: []
---

## Scope

- Move the response-dict building (`_product_dict`, `_variant_dict`, `_list_dict`, `_low_stock_dict`, category and image dicts) out of `catalog/api.py` into a services module the API only calls.
- Give the owner-only field policy one home: field-level input rules, output omission and the archive permission, called by the handlers instead of three inline checks.
- Let `catalog.update_product(actor, pk, patch)` own id resolution and patch-key translation; handlers stop re-fetching after every write.

## Acceptance

- [x] Behaviour is unchanged: the existing admin catalog tests pass untouched, and OpenAPI output for the admin API is identical (`openapi-admin.json`).
- [x] `catalog/api.py` holds routing and schemas only; permission-matrix tests exist at the service level.
- [x] Full suite green.

## Spec

- [Phase 2 section 6](../../spec/phase-2.md)
