---
title: Frontend: Dashboard category tree
repo: store-frontend
slice: 17
type: AFK
status: closed
assignee:
blocked-by: [69-be-category-tree.md]
---

## Scope

- Replace the grouped lists on the categories page with one expandable tree up to five levels, with expand all and collapse all.
- Each row: thumbnail if any, name, direct Product count, child count, a "hidden" badge (also when hidden through a parent), and actions: add child, edit, hide, move, delete. Add child is disabled at depth 5.
- Reorder with up and down buttons among siblings. "Move to..." opens a parent picker (a tree) that disables the Category itself, its descendants and parents that would pass depth 5; the move carries the subtree.
- Delete uses the shared confirm dialog from ticket 73, or a plain dialog until it lands; a 409 shows the blocking counts.
- Update `category-order.ts` (which assumes two levels) and its tests.

## Acceptance

- [x] A five-level tree renders, expands and collapses; the "Move to..." picker blocks invalid targets.
- [x] Reordering and moving work by keyboard and touch with no drag and drop.
- [x] Staff do not see move or delete.

## Spec

- `GLOSSARY.md` (Category), [ADR 0005](../../adr/0005-deletes-are-owner-only-and-refused-while-referenced.md)
