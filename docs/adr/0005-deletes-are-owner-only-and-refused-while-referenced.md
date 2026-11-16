# Deletes are Owner-only and refused while something still references the record

Every hard delete of store data (Category, Product, Variant, Brand, Supplier, Colour, and the existing Product class, Attribute and photo deletes) is a Destructive action: Owner-only, confirmed in the Dashboard with the item's name and the number of rows it touches, and written to the Audit log. A delete is refused with a 409 that says how many records block it; nothing is cascaded silently.

- **Category**: only when it has no child Categories and no Products. Hiding stays the soft option. The deleted Category's URL returns 404 and leaves the sitemap; there are no redirects.
- **Product**: only when it has no Orders (the existing `delete_product` rule). Otherwise the Owner archives it.
- **Variant**: only when it has no Orders, and never the last Variant of a published Product.
- **Brand and Supplier**: only when no Product uses them.
- **Colour**: only when no Variant or photo uses it. An in-use Colour is deactivated instead (`is_active`).
- **Menu items** ([ADR 0007](0007-menu-items-reference-their-target.md)): a Category, Product or Brand that a Menu item points to cannot be deleted; the 409 says how many Menu items block it, and the Owner removes the Menu item first.
- Product classes, Attributes and photos keep the rules they already have.

Why: Orders keep a stable history and a Product never loses its Category or Brand by accident. The Audit log records who deleted what, so a mistake can be traced; because the records are gone, the refusals do the protecting, not the log.

We rejected detaching or cascading (delete a Category and uncategorise or delete what is under it): one click could empty a section of the shop, and an Owner who wants that can move or delete the contents first, which the dialogs make explicit. We also rejected letting Staff delete: Staff already cannot archive, and a delete is the same kind of action.

Consequences: each delete needs a "what blocks this" count in the service so the API and the Dashboard dialog can name it.
