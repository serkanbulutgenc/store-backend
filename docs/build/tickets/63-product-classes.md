---
title: Product classes: Owner creates and deletes them
repo: store-backend + store-frontend
slice: 16
type: AFK
status: closed
assignee:
blocked-by: []
---

## Scope

- Admin API: create a Product class (name, one of the existing size schemes from `catalog/sizes.py`, default KDV rate, hygiene flag, default desi) and delete one. Delete is refused while any Product uses the class. Edit already exists.
- The size scheme is locked once the class has any Product; the API refuses a change and the form shows the field read-only with the reason.
- A changed default KDV rate applies to new Products only (order lines already snapshot KDV). Say so in the edit form.
- Create, edit and delete are Owner-only: new permission `manage_product_classes` added to the Owner group by migration; the guard test's Owner-only set is updated in the same change. Audit log entry for each.
- Dashboard: the existing Product classes screen gains "Yeni sınıf" and delete (with confirmation), hidden from Staff and refused by the route.
- No custom size lists: a new class picks from the existing schemes.

## Acceptance

- [x] The Owner creates a class, sees it in the product editor's class picker and in the import's known classes.
- [x] Deleting a class that has a Product is refused with a clear message; an unused class deletes.
- [x] Changing the size scheme of a class with Products is refused; a class with none can change it.
- [x] A KDV rate change leaves existing Products and order snapshots untouched.
- [x] Staff get 403 on create, edit and delete; the guard test lists them.

## Spec

- CONTEXT.md terms Product class and Size scheme; [ADR 0001](../../adr/0001-staff-trusted-with-correctable-actions.md).
