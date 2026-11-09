---
title: Prototype: compact shadcn design language for storefront and dashboard
label: wayfinder:prototype
status: closed
assignee: claude
blocked-by: [03-research-tanstack-start.md]
parent: map.md
---

## Question

Make a rough, throwaway UI prototype to react to: storefront product page + checkout, and a dense admin order/product table, using shadcn with a compact density. Decide density, typography, colour direction fit for a women's fashion boutique, and what 'compact and easy to use' concretely means.

## Resolution

Prototype: [prototype/index.html](../prototype/index.html) (static HTML, 3 variants x product / checkout / admin orders; throwaway, kept as the primary source). Not committed to a branch: the backend docs dir was not a git repo when this closed.

**Storefront = variant B (boutique / balanced); admin dashboard = variant C (utilitarian / compact).** The two surfaces intentionally use different densities.

Storefront (B), as shown in the prototype:
- 15px base type, Inter for headings and body, 10px radius, ~18px card padding, ~48px table rows.
- Plum accent (#6b2d5c) on a near-white pink-tinted background; white cards with soft lavender borders.
- Product page: thumbnail rail, main photo, info card (price shown "KDV dahil", colour chips, size chips with struck-out unavailable sizes, add-to-cart, stock/shipping/COD line).
- Checkout: two columns, step cards (contact, address, payment) on the left, sticky order summary, consent checkbox and confirm button on the right; summary lists shipping and COD fee lines.

Admin (C), as shown in the prototype:
- 13px base type, 6px radius, ~10px padding, 32px rows, monochrome zinc with near-black primary; status colours only on badges.
- Narrow left sidebar, search plus dropdown filters in the page header, row checkboxes with a bulk-action strip (mark paid, ship, cancel), separate payment-status and fulfilment-status badges, pagination footer.
- Variant C's colour x size stock grid is the model for the product-variant stock view.

Not decided here (left to implementation): exact shadcn theme tokens, final fonts, product photography direction, mobile breakpoints for the admin.
