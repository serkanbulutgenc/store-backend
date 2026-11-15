---
title: Backend: deeper categories in import, browse, search and sitemap
repo: store-backend
slice: 17
type: AFK
status: closed
assignee:
blocked-by: [69-be-category-tree.md]
---

## Scope

- Import: the `category` column accepts `A > B > C > D > E` (up to 5 parts); missing ancestors are created on confirm; more than 5 parts is a row error on `category`. Generalise the roots-plus-one-child lookup in `catalog_import.py`. Update the template CSV and the module docstring.
- Browse, search and menu code written for depths 1-2 (`browse.py`, `search.py`, `store_api.py`, `catalog_views.py`) works at any depth: the menu endpoint returns the tree with a depth parameter or the first two levels plus child counts, a Category listing includes its descendants' Products, and each Category returns its direct children and its full breadcrumb path.
- A Product may sit in a Category at any level.
- Hidden Categories still take their subtree off the Storefront; deleted Categories drop out of the sitemap.

## Acceptance

- [x] A five-part import path creates the chain once and re-importing does not duplicate it.
- [x] A listing of a mid-level Category includes Products of all its descendants.
- [x] The Category endpoint returns children and breadcrumbs for a depth-4 Category; hiding a depth-2 Category hides depth 3-5.

## Spec

- `GLOSSARY.md` (Category). The Storefront side (children links, breadcrumbs, mega menu) is built in the storefront round.
