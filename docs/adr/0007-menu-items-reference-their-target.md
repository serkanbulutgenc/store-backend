# Menu items reference their target and block its deletion

A Menu item points at its target (Category, Legal page, Product, Brand, built-in page) by reference, not by a stored label and URL. The label and link follow the target when it is renamed or its slug changes; the Owner may override the label. A target that is not public (hidden, draft, archived) is left out of the Storefront. A target cannot be deleted while a Menu item points to it: the delete is refused with a 409 that says how many Menu items block it, extending [ADR 0005](0005-deletes-are-owner-only-and-refused-while-referenced.md). The Owner removes the Menu item first.

Why: slugs change and Categories are renamed, so a copied URL would turn into a silent 404 in the most-visited part of the Storefront. Referencing keeps the Menu correct without the Owner revisiting it, and it lets an automatic Category subtree stay in step with the Category tree.

We rejected:
- **Copy** (store the label and URL on the item): simple, but it goes stale and breaks without anyone noticing.
- **Cascade** (deleting a target removes its Menu items): the same silent side effect ADR 0005 turned down, and a delete would reshape the navigation without the Owner seeing it.
- **Skip and flag** (never block the delete, mark orphaned items as broken in the Dashboard): leaves dangling rows that every Storefront render has to filter.

Consequences: every deletable target type needs a "used by N Menu items" count in the service so the API and the Dashboard dialog can name it. Page types added later must register as a Menu target and provide the same count.
