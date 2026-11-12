---
title: Backend: catalog models and services
repo: store-backend
slice: 3
type: AFK
status: closed
assignee:
blocked-by: [05-be-oscar.md]
---

## Scope

- Product class (size scheme, default KDV rate, hygiene flag), Colour list with swatch hex, two-level Category, optional Brand, Supplier with code and cost price, Product (draft/published/archived), Variant (colour x size, SKU, optional barcode, stock). Vocabulary: `CONTEXT.md` in the backend repo.
- Auto SKU generator (e.g. `AUR-000123-KRM-38`, editable, unique); archive instead of delete for Products with Orders.
- Shelf price on the Product with Variant override, optional old price, low-stock threshold (default 2, per-Product override).
- Slug generation with Turkish transliteration and a redirect table for old slugs.

## Acceptance

- [x] Migrations apply; services enforce one primary Category and fixed size lists (tests).
- [x] Hard delete of a Product with Orders is rejected.
- [x] Slugs are unique and transliterated; renaming a slug records a redirect.

## Spec

- [PRD section 5](../../spec/prd.md)
