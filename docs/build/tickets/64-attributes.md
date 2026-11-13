---
title: Attributes: per-class definitions, values, product-page block, search and facets
repo: store-backend + store-frontend
slice: 16
type: AFK
status: closed
assignee:
blocked-by: [63-product-classes.md]
---

## Scope

- Own models, not Oscar's `ProductAttribute` ([ADR 0003](../../adr/0003-attributes-favorites-reviews-are-own-models.md), written in this ticket). Attribute definition belongs to a Product class: name, type (text or option list), single- or multi-value (fixed at creation), filterable flag, sort order. Options belong to one attribute (never shared). Attribute value belongs to a Product.
- All attributes are optional; adding one to a class backfills nothing and an empty value is not shown.
- Admin API and Dashboard: definitions are Owner-only (permission `manage_attributes`, migration adds it to the Owner group, guard test updated); Staff fill in values on the product editor. Deleting an attribute or option that has values is a Destructive action: confirmation that states how many Products are affected. Audit log entries.
- Store API: the product detail carries its attribute values; storefront shows a "Ürün özellikleri" block on the product page.
- Search: text and option labels of attribute values are part of `ProductProfile.search_text`, rebuilt when values change.
- Facets: filterable attributes appear as extra facets on category and search listings with the existing rules (AND across facets, "only in stock" default, counts).
- The bulk import is unchanged in this ticket.
- Docs: CONTEXT.md terms Attribute and Attribute value; ADR 0003 (shared with 67 and 68).

## Acceptance

- [x] The Owner defines "Kumaş" (option list, single) and "Sezon" (multi) on Giyim; Staff set values on a Product; the product page shows them.
- [x] Searching an option label finds the Product.
- [x] A filterable attribute shows as a facet with correct counts and combines with colour and size filters.
- [x] Deleting an option in use asks for confirmation with the affected count and removes the values.
- [x] Staff get 403 on definition endpoints; the guard test lists them. Existing Products render unchanged.

## Spec

- Decisions of 2026-11-13 Admin features interview; CONTEXT.md; architecture.md "Domain additions".
