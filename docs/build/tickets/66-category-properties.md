---
title: Category properties: description, image, SEO, order, hidden
repo: store-backend + store-frontend
slice: 16
type: AFK
status: closed
assignee:
blocked-by: []
---

## Scope

- Category gains description, image (reusing the product photo pipeline: upload, derivatives, storage, pending-file removal), SEO title, SEO description, a sort order (integer, per sibling group) and an active/hidden flag.
- Hidden: the category and its subtree leave the Storefront category tree, listings and sitemap, and its own URL returns 404. Products that also sit in a visible category stay visible; Products only in hidden categories are reachable by their own URL and search as today (decide in the PR and record it if search should exclude them).
- Dashboard: category editor gets the fields; the category list reorders by drag and drop within a sibling group and saves the new order in one call. Staff may edit (correctable); no delete changes.
- Storefront category page shows the description and uses the SEO fields in metadata; the home and menus follow the sort order, then name.

## Acceptance

- [x] Reordering siblings changes the Storefront menu order without a deploy.
- [x] Hiding a parent hides its children everywhere on the Storefront and its URL returns 404.
- [x] A Product in both a hidden and a visible category still lists under the visible one.
- [x] The image shows on the category page with the same derivative sizes as product photos.
- [x] SEO title and description appear in the page metadata; the sitemap omits hidden categories.

## Spec

- CONTEXT.md term Category; [media storage](../../wayfinder/tickets/15-media-storage.md).
