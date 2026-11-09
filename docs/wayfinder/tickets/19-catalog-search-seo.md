---
title: Grill: catalog browsing, search, filtering and SEO
label: wayfinder:grilling
status: closed
assignee: claude
blocked-by: []
parent: map.md
---

## Question

Decide how Shoppers find products: category listing, filters (size, colour, price, brand, availability), sorting, text search (Turkish language: dotted/dotless i, diacritics, typos), the search backend given Oscar's haystack dependency and a single VPS, what the API returns for list pages and how it pages, and the SEO basics for an SSR storefront: URL scheme and slugs (Turkish storefront paths), canonical URLs, metadata, structured data for products, sitemap and robots, and handling of draft/archived/out-of-stock products.

## Resolution

Decided in a two-round grilling; all recommendations accepted.

**Search and filters**
- Text search runs on Postgres full-text search with a Turkish configuration and `unaccent`, plus a trigram index for typo tolerance; Turkish dotted/dotless i normalised on both sides. Covers name, SKU, brand and category name. One search-service module is the swap point if Meilisearch is wanted later. Oscar's haystack stays the no-op simple backend.
- Filters: category (the page), colour, size, brand, price range (min/max), "only in stock" toggle (on by default). Sorts: newest (default), price ascending, price descending. "Yeni" and "indirimde" are chips/pages from existing flags. Filter state lives in URL query params.
- Filters match Variants but results are Products: a Product matches size if any Variant of that size has stock, colour likewise; with both set, one Variant must satisfy both. Facet counts respect the other active filters.

**Visibility and API**
- Draft and archived Products return 404. A published sold-out Product stays visible, marked "Tukendi", sorted after in-stock ones, cannot be added to the basket, and is hidden from listings only while "only in stock" is on.
- One store endpoint `GET /api/store/products` (category, `q`, filters, `sort`, `page`) serves category pages and search, returning product cards (name, slug, primary image with srcset sizes, price, old price, sold-out flag, colour swatches), facet counts and total. Page size 24, "daha fazla goster" link, no infinite scroll.

**URLs and SEO**
- `/urun/<slug>` (no category in path); `/kategori/<slug>` and `/kategori/<parent>/<child>`; search at `/ara?q=`; paging `?sayfa=2`. Slugs transliterate Turkish characters, are unique, editable by Staff, and old slugs 301 to the new one.
- Category pages: canonical is the unfiltered URL plus the page parameter, indexable. Filtered, sorted and search pages: `noindex, follow`. Product canonical ignores the colour query.
- Product pages: Turkish title, meta description from the first 155 characters of the description (template if empty), Open Graph and Twitter tags using the primary image, `Product` JSON-LD (name, images, description, SKU, brand, TRY KDV-inclusive price, availability from stock). Categories get `BreadcrumbList`; the homepage gets `Organization` from Store settings. All rendered on the server.
- Django generates `sitemap.xml` (published Products, categories, brands, legal pages, `lastmod`) via the single public origin; `robots.txt` allows the storefront, disallows `/admin`, `/sepet`, `/odeme`, `/hesabim`, `/siparis-takip` and the API, and links the sitemap. Google Search Console submission is on the Owner's launch checklist.
- No per-product or per-category SEO fields in phase 1; titles and descriptions come from templates. The product description is the only free text Staff write.

**Dashboard search and storefront navigation**
- Dashboard search is separate: case-insensitive contains with Turkish normalisation plus exact match on SKU, barcode and order number (exact first, so scanning a barcode opens the Product); not full-text. Staff can search Customer phone and email since they already see them.
- Header menu lists top-level categories with a simple dropdown of children (no mega menu). Homepage: one optional banner (image and link, a Store setting), "Yeni gelenler", and the categories. No curation, recommendations or recently-viewed. Empty search results show a message, top categories and a contact link. Product pages show "benzer urunler" from the same category.
