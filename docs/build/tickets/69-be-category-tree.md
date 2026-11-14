---
title: Backend: category tree to five levels, move and delete
repo: store-backend
slice: 17
type: AFK
status: open
assignee:
blocked-by: []
---

## Scope

- Raise `MAX_CATEGORY_DEPTH` from 2 to 5 and fix the "two-level" wording in errors and docstrings. No limit on top-level Categories or on children per Category.
- Move endpoint (Owner): change a Category's parent, with its whole subtree. Refused with a message naming the rule when the target is the Category itself or one of its descendants, or when the deepest descendant would pass depth 5. Position among the new siblings is last unless given. Audit entry.
- Delete endpoint (Owner): only a Category with no children and no Products (Products have one primary Category); otherwise 409 with the child and Product counts. Removes its image files like `remove_image` does. Audit entry; confirmed per ADR 0005.
- The admin category view-model exposes depth, child count and direct Product count, so the tree needs no extra calls.
- Update `catalog/api.py`, the OpenAPI export, and tests for depth 5, the move rules, the delete guards and Staff getting 403.

## Acceptance

- [ ] A Category can be created at depth 5 and not at depth 6.
- [ ] Moving a Category under its own descendant, or past depth 5, is refused with a clear message; a valid move carries the subtree and keeps sibling order consistent.
- [ ] Deleting a Category with children or Products returns 409 with counts; an empty one is deleted and audited; Staff get 403.

## Spec

- [ADR 0005](../../adr/0005-deletes-are-owner-only-and-refused-while-referenced.md), `CONTEXT.md` (Category)
