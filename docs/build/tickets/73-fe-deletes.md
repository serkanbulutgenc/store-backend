---
title: Frontend: delete buttons and confirm dialogs
repo: store-frontend
slice: 17
type: AFK
status: closed
assignee:
blocked-by: [71-be-deletes.md, 72-fe-category-tree.md]
---

## Scope

- One shared confirm dialog: names the item, shows what the delete touches, and shows the 409 blocking counts when the backend refuses.
- Delete actions, Owner only, on Products (list and editor; Archive stays as the alternative when Orders exist), Variants, Brands, Suppliers and Colours (Deactivate offered for an in-use Colour). The category tree uses the same dialog.
- Check the existing deletes (product classes, attributes, options, photos) use the same dialog and wording.

## Acceptance

- [x] Every resource in ADR 0005 has a delete action for the Owner and none for Staff.
- [x] A refused delete tells the Owner what blocks it; a successful one refreshes the list.

## Spec

- [ADR 0005](../../adr/0005-deletes-are-owner-only-and-refused-while-referenced.md)
