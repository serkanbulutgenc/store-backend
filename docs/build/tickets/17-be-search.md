---
title: Backend: product list endpoint, full-text search and facets
repo: store-backend
slice: 4
type: AFK
status: closed
assignee:
blocked-by: [10-be-catalog.md, 06-be-api.md]
---

## Scope

- `GET /api/store/products` with category, `q`, colour, size, brand, price range, in-stock toggle, sort (newest, price asc/desc), `page`; 24 per page; product cards with primary image sizes, price, old price, sold-out flag, colour swatches; facet counts respecting other filters.
- Postgres full-text search with a Turkish configuration, `unaccent`, trigram index and dotted/dotless-i normalisation behind one search-service module.
- Variant-level matching for size and colour (both set means one Variant satisfies both); draft and archived return 404.

## Acceptance

- [x] Build check 6: search for words with ı/İ/ş/ğ finds the expected Products and tolerates a typo (tests).
- [x] Facet counts change correctly with active filters.

## Spec

- [Architecture section 2](../../spec/architecture.md)
- [PRD section 2](../../spec/prd.md)

## Notes

- Build check 6: `fold()` maps I, ı, İ and the Turkish letters to plain ASCII on the document and the query side (Python, not the `unaccent` extension, because `str.lower()` and database locales disagree on dotted/dotless i). Tests cover ışık/ISIK/İstanbul/çiçek/ağaç and typos ("elbse", "cicekly").
- Search text and vector live on `ProductProfile` and are rebuilt by signals; the Turkish `search_vector` has a GIN index and `search_text` a trigram GIN index. Typo tolerance filters on `word_similarity >= 0.5` computed in SQL; that form does not use the trigram index, which is fine at this catalog size and keeps the threshold explicit. `django.contrib.postgres` is now installed and migration 0004 enables `pg_trgm`.
- Also added `GET /api/store/products/{slug}` (the 404 rule for draft and archived needs it). Old-slug redirects are not handled there yet (ticket 19).
- Facets: colours, sizes, brands, price min/max. No category facet; the category tree endpoint for the header menu is not built here.
- Price filter, sort and card price use the cheapest Variant (override or shelf price). `old_price` is shown only when that equals the shelf price.
