---
title: Favorites: Shoppers heart Products
repo: store-backend + store-frontend
slice: 16
type: AFK
status: closed
assignee:
blocked-by: []
---

## Scope

- Own model `Favorite` (Shopper, Product, created at; unique pair), not Oscar's wishlists ([ADR 0003](../../adr/0003-attributes-favorites-reviews-are-own-models.md)). Per Product, not per Variant.
- Store API (logged-in Shoppers only; guests get 401): add, remove, list. Limit 200 per Shopper. The list hides draft and archived Products and returns sold-out Products flagged so the UI greys them out. No public count anywhere and no notifications (marketing would need IYS).
- Storefront: a heart on the product page and listing cards; a guest who clicks it is sent to login and returns to the page with the heart applied; "Favorilerim" in the account area. Deleting an account removes its Favorites (add to the account-deletion service and its test).
- Docs: update prd.md and phase-2.md ("No wishlist"), CONTEXT.md term Favorite.

## Acceptance

- [x] Hearting twice is idempotent; unhearting removes it; the 201st is refused with a clear message.
- [x] Archived and draft Products do not appear in the list; a sold-out one appears greyed out.
- [x] A guest hearting is redirected to login and the heart is applied on return.
- [x] Account deletion removes the Shopper's Favorites.
- [x] Dashboard users (is_staff) still get 403 on the Store API.

## Spec

- Decisions of 2026-11-13 Admin features interview.
