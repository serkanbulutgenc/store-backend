---
title: Infinite scroll, name and rating sorts for the catalogue listing
repo: store-frontend
slice: 18
type: AFK
status: closed
assignee:
blocked-by: [18-fe-browse.md]
---

## Scope

- The category and search listing (`product-listing.tsx`) loads the next page when the end comes near (IntersectionObserver sentinel, 600 px ahead). It bumps `?page=N` in the URL with `replace`, so Back leaves the list and a reload or a shared link shows the same N pages.
- "Daha fazla göster" is now a real `rel="next"` link to `?page=N+1`, so crawlers and keyboards can follow it. It stays visible as the fallback.
- States: skeleton cards in the grid while the next page loads (also when a filter or sort is applied: the old facets stay, the grid shows skeletons; the browser's loader no longer waits for products, the server preloads up to 3 pages), an error row with "Tekrar dene", "Tüm ürünleri gördünüz." at the end, and an `aria-live` count ("N / total ürün gösteriliyor").
- The 10-page cap is gone. The loader preloads at most 3 pages (`SSR_PAGES`); deeper pages load on the client. `?page=` is bounded at 500 and clamped to the last real page.
- Backend: `GET /api/store/products` takes two more sorts, `name_asc` (Turkish collation `tr-x-icu`) and `rating_desc` (published reviews, unrated last). Sold-out products still sort last and `-product_id` still breaks ties. The pagination stays offset-based.
- Playwright spec `e2e/tests/storefront-infinite-scroll.spec.ts` in store-infra.

## Acceptance

- [x] Scrolling to the end of a 30-product category loads the second page without a click.
- [x] The URL holds the page; a deep link and Back from a product show the loaded list.
- [x] Sorting by name A-Z and by rating works and resets the page.
- [x] The next-page control is a link with `rel="next"`.

## Not in this ticket

- Best-selling sort, sort by any attribute, an "on sale" filter.
- Sold-out swatch marking and the header search prefill from `?q=` (ticket 18 notes).

## Spec

- `docs/spec/architecture.md` (pagination convention)
- Ticket 18
