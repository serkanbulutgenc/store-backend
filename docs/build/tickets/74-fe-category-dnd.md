---
title: Frontend: drag and drop in the category tree
repo: store-frontend
slice: 17
type: AFK
status: closed
assignee:
blocked-by: [72-fe-category-tree.md]
---

## Scope

- Add `@dnd-kit` drag and drop to the tree for reordering among siblings and for re-parenting, using the same move and reorder endpoints as the buttons.
- Keyboard sensor on, buttons and "Move to..." stay as the fallback. Invalid drops (own subtree, past depth 5) show as not droppable.

## Acceptance

- [x] Dragging reorders and re-parents with the same results as the buttons.
- [x] Everything still works without a pointer.

## Spec

- `GLOSSARY.md` (Category)
