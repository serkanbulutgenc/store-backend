---
title: Frontend: storefront catalogue pages
repo: store-frontend
slice: 4
type: AFK
status: closed
assignee:
blocked-by: [09-fe-proxy.md, 17-be-search.md, 15-be-media.md]
---

## Scope

- Header with category dropdown, homepage (optional banner, "Yeni gelenler", categories), category listing and `/ara` with filters and sorts in URL params, "daha fazla goster", product page with colour-filtered gallery, selectors, sold-out state, "benzer urunler", "son N adet", hygiene note.
- SSR with Query streaming; empty search shows top categories and a contact link.

## Acceptance

- [x] Listing state is shareable via URL and survives reload and back.
- [x] A sold-out Variant cannot be added to the basket (UI state).

## Spec

- [PRD section 2](../../spec/prd.md)

## Notes

- Routes: `/kategori/$slug`, `/ara`, `/urun/$slug`. Listing state is URL search params; `page=N` shows pages 1 to N (capped at 10), so "daha fazla goster" survives reload and back.
- Added `GET /api/store/categories` (two-level tree) because ticket 17 did not build the header menu endpoint.
- Not done here: the optional homepage banner (needs Store settings, ticket 20); "Sepete ekle" has no handler until the basket (21/23); the empty-search contact link points at `/legal/iletisim` (ticket 23).
- Loaders await every page, so nothing streams yet, and "benzer urunler" loads client-side. True Query streaming needs the router SSR-query integration package.
- Known small gaps: "daha fazla goster" is a button, not a link; sold-out colour swatches are not marked; the header search box does not prefill from `?q=`.
