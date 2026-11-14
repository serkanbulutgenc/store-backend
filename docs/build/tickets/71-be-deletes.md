---
title: Backend: delete routes for products, variants, brands, suppliers, colours
repo: store-backend
slice: 17
type: AFK
status: closed
assignee:
blocked-by: []
---

## Scope

- `DELETE` routes, Owner-only and audited, for Product (wrap `delete_product`), Variant, Brand, Supplier and Colour, following ADR 0005: Product and Variant need no Orders, and a Variant is never the last of a published Product; Brand, Supplier and Colour only when unused. Refusals are 409s with the blocking count.
- Deleting removes the photo files and slug redirects of a deleted Product (through `PendingFileRemoval`) and reindexes search.
- Services expose a "what blocks this" count so the Dashboard dialog can show it before the call.
- Update the OpenAPI export and tests, including Staff 403.

## Acceptance

- [x] Each resource deletes when unreferenced and returns 409 with a count when referenced.
- [x] The last Variant of a published Product cannot be deleted.
- [x] Each delete writes an Audit entry; Staff get 403.

## Spec

- [ADR 0005](../../adr/0005-deletes-are-owner-only-and-refused-while-referenced.md)
