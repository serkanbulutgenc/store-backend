# Aura Tekstil Store

Glossary for the Aura Tekstil online store. Terms only; no implementation detail.

## Language

**Aura Tekstil**: The physical women's clothing, underwear, shoes and accessories shop in Mersin that this online store extends.

**Storefront**: The public site where shoppers browse and buy.

**Dashboard**: The site for Dashboard users to manage the store. Separate from the Storefront.

**Shopper**: Anyone buying on the Storefront. A Dashboard user is never a Shopper: they have no Storefront account, so testing a purchase needs a separate Customer account.
- **Guest**: A Shopper who buys without an account.
- **Customer**: A Shopper with an account (email and password).

**Dashboard user**: A person who works in the Dashboard. Holds exactly one of two roles: **Owner** or **Staff**.
- **Owner**: The role with unrestricted access to the financial and structural core of the business: Settings, Dashboard users, the Colour list, cost prices and Supplier data, archiving, refunds, cancelling paid Orders, account deletion requests, and all Revenue data.
- **Staff**: The everyday role, limited to execution: processing Orders (including marking them paid), managing Products and stock. Sees each Order's own amounts, never Revenue data, and cannot take Owner-only actions.
- **Revenue data**: Anything that aggregates money across Orders (sales totals, trends, exports such as the accountant CSV) and anything that reveals margin (cost prices, Supplier data). A single Order's amounts are not Revenue data, because executing that Order needs them (for example the cash to collect on delivery).
- **Destructive action**: An action that sends money out or removes something for good: archiving a Product, recording a refund, cancelling a paid Order, handling an account deletion request. Owner-only. So is every hard delete (see [ADR 0005](docs/adr/0005-deletes-are-owner-only-and-refused-while-referenced.md)). Mistakes that can be corrected by editing, such as a wrong stock count or price, are not destructive; the Audit log is their safety net. Marking an Order paid only records money that has arrived, so Staff may do it, though Staff cannot undo it.
- **Invitation**: An Owner's email invite through which a new Dashboard user sets a password. Dashboard users are deactivated, never deleted.

**Audit log**: Append-only record of sensitive Dashboard actions (who, when, what, before and after), shown on the Order and as an Owner-only list.

**Product**: A sellable model as shoppers see it (e.g. a dress), which comes in several **Variants** (colour x size). Belongs to one primary **Category** and one **Product class**. Is draft, published or archived.

**Variant**: One purchasable colour/size combination of a Product, with its own stock, **SKU** and optional barcode.

**Product class**: A kind of Product (clothing, footwear, underwear, accessory) that fixes its size scheme, default KDV rate, hygiene flag and default desi. The Owner creates, edits and deletes classes; a class with Products cannot be deleted and its size scheme is locked. A changed default KDV rate applies to Products created afterwards only.

**Size scheme**: The fixed list of sizes allowed for a Product class (letters, numbers, shoe sizes, bra sizes, one-size).

**Attribute**: A descriptive property of a Product class (for example Kumaş or Sezon), defined by the Owner. It is a text field or an option list; an option list holds one value or several, fixed when the attribute is created, and may be marked filterable for the Storefront. Options belong to one attribute and are never shared. Attributes are optional: adding one backfills nothing and an empty value is not shown.

**Attribute value**: What a Product holds for one Attribute: a text, or the chosen option or options. Staff fill them in on the Product. Deleting an Attribute or an option that Products use removes those values, which is a Destructive action: the Dashboard says how many Products are affected and the API requires confirmation.

**Colour**: An entry in the Owner-managed shared colour list, with a swatch.

**Category**: A node in a tree up to five levels deep (e.g. Giyim > Elbise > Midi Elbise); the top level and the number of children per Category are unlimited. A Product has one primary Category, at any level. A Category can be moved with its whole subtree (never under itself or its own descendants, never so that the tree passes five levels) and can be deleted only when it has no children and no Products; its URL then returns 404.

**Brand**: The maker label of a Product (e.g. a clothing label). Optional.

**Supplier**: A company Aura Tekstil buys stock from. Internal only; Suppliers never sell on the Storefront. A Product has one Supplier, with a supplier code and cost price.

**SKU**: Unique, auto-generated (editable) code identifying a Variant.

**Shelf price**: The KDV-inclusive price shown to Shoppers. Set on the Product; a Variant may override it.

**Old price**: Optional earlier shelf price shown struck through to signal a discount. A catalogue markdown that needs no code, unlike a **Voucher**.

**Voucher**: A code a Shopper (Guest or Customer) enters at checkout to reduce the price of the items in their basket, either by a percentage or by a fixed TL amount. Only the Owner creates and manages Vouchers; Staff see which Voucher an Order used. An Order carries at most one Voucher, and it applies to every item, including those showing an Old price. An Owner can limit a Voucher with an expiry date, a minimum basket amount, a total redemption cap and an optional "once per Customer" limit, which matches a Guest by the email on the Order.
- **Redemption**: One Order's use of a Voucher. Counted when the Order is placed and released if the Order is cancelled, including the automatic cancel after the Payment window.
- **Voucher discount**: The amount a Voucher takes off an Order's items, spread across the item lines in proportion to their value. Shipping and COD fees are never discounted, the minimum basket amount and the free-shipping threshold are measured before it, and it cannot take the items below zero. It stays frozen on the Order, and a refund returns what the Shopper actually paid for a line, net of its share.
- A Voucher that has been redeemed can be deactivated but never deleted.

**Campaign**: An automatic, code-free discount the Owner sets on chosen Products, Categories (optionally with their subcategories) or Collections. It takes a percentage or fixed TL amount off the Shelf price, so the Shopper simply sees the lower price, with the Shelf price struck through and a "-20%" badge. Only the Owner creates, edits, activates, deactivates and deletes Campaigns; Staff see which Campaign an Order used. There is no approval step, and every change is written to the Audit log. A Voucher still applies on top of a Campaign price.
- A Campaign has an optional start and end date-time that apply it automatically, plus a manual active/inactive switch that overrides the schedule. An open-ended Campaign is allowed.
- The discount is a percentage only. It applies to each Variant's effective Shelf price, so a Variant's own override is discounted too.
- A Product under several active Campaigns gets only the largest discount; Campaigns never stack. The Dashboard shows which Campaign is winning on a Product.
- The struck-through price is the Shelf price, not the Old price, which is hidden while a Campaign is active on the Product.
- The basket and checkout always use the current Campaign price, and the basket flags a price that changed. The free-shipping threshold and a Voucher's minimum basket amount are measured on Campaign prices.
- The Campaign form warns the Owner about Products pushed below cost price, but does not block.
- A Campaign that an Order used can be deactivated but never deleted (see [ADR 0005](docs/adr/0005-deletes-are-owner-only-and-refused-while-referenced.md)).
- The Shopper sees only the price, strike-through and "-20%" badge; a Campaign has an internal name, no public name or landing page.
- An Order freezes the Campaign price it was bought at and links to the Campaign for the Owner's reporting. A refund returns what the Shopper actually paid.

**Archived**: A Product hidden from the Storefront but kept for Order history. Products with Orders are never deleted.

**Sold out**: A Variant with zero stock. Still shown, but cannot be bought.

**Low-stock threshold**: Stock level at or below which the Dashboard flags a Variant and the Storefront shows "son N adet" (default 2, overridable per Product).

**Order**: A Shopper's purchase. Has a **Fulfilment status** and a separate **Payment status**.
- **Fulfilment status**: Where the goods are: new, preparing, shipped, delivered, completed, cancelled, return requested, returned.
- **Payment status**: Whether money has been received: unpaid, paid, refunded.

**Return request**: A Shopper's request to send back chosen lines and quantities of a delivered Order, within 14 days of delivery and never for hygiene lines. Has its own status (requested, approved, rejected, received, refunded) and leaves the Order's Fulfilment status alone, so a partial return does not mark the whole Order returned.

**Bank transfer**: Payment method where the Shopper sends money to the shop's bank account quoting the **Order number**; a Dashboard user marks the Order paid after seeing it arrive.

**Cash on delivery (COD)**: Payment method where the carrier collects cash on delivery; the Order becomes paid when delivered.

**Order number**: Short unique reference identifying an Order, used as the bank transfer description.

**Payment window**: The 48 hours a bank transfer Order has to be paid before it is cancelled automatically.

**Guest order link**: A signed link in the confirmation email that opens one Order without login.

**Order tracking**: A public page where a Shopper enters an Order number plus the email or phone on the Order to see its status.

**Address book**: A Customer's saved delivery addresses (up to five, one default).

**Favorite**: A logged-in Shopper's heart on a Product (not a Variant), up to 200. Private: no public count and no notifications. Draft and archived Products are hidden from the list; sold-out ones show greyed out.

**Review**: A verified buyer's 1-5 star rating of a Product with optional text, shown as "Ayşe K.". Only a Shopper with a delivered, not fully returned Order line for the Product may write one; one per Shopper and Product. Stars alone publish at once; any text (even emoji only) waits in the moderation queue until Owner or Staff approve it, and editing the text sends it back. Text is basic markdown without HTML, headings, images or clickable links.

**Claiming**: Attaching a Guest's Orders to a Customer account once the account's email is verified and matches the Order's email.

**Legal page**: A fixed public page carrying contract, policy or notice text supplied by the lawyer (distance sales contract, pre-information form, returns and withdrawal, privacy notice, delivery, payment methods, about and contact). The Owner edits it in the Dashboard in Markdown. Shoppers can search the Legal pages.
- **Legal page version**: One saved text of a Legal page. Every save creates a new version; a version never changes afterwards, so a Consent record always points to the exact text a Shopper accepted.

**Menu**: A navigation list the Shopper sees on the Storefront. There are exactly two, the **Header menu** and the **Footer menu**; the Owner fills them but cannot add or remove a Menu. Only the Owner edits Menus, and a change is live at once and written to the Audit log. A Menu has no versions.
- The Header menu nests up to three levels; the Footer menu two (a group heading, then its links). The Header menu can never be saved empty. The Footer menu must always contain a link to the Legal page index, though any single Legal page may be left out. Both start out matching the Storefront as it was before Menus existed: the Header holds the Category tree, the Footer every Legal page plus Legal page search and Order tracking.
- **Menu item**: One entry in a Menu. It points to a **target** by reference, so its label and link follow the target when it is renamed or its URL changes; the Owner may override the label. A target is a Category (optionally with its subcategories included automatically), a Legal page, a Product, a Brand, a Collection, a built-in Storefront page (Home, Order tracking, Favorites, Basket), a custom URL, or a group heading that links nowhere. Page types added later become targets too. A custom URL is an internal path starting with `/` or an `https://` address; an external one opens in a new tab. A Category item shows its Category's image; no other target has one. Subcategories included automatically stop at the Menu's depth limit. The same target may appear more than once. The Owner can hide a Menu item without deleting it; hiding a group heading hides its children too.
- A Menu item whose target is not public (hidden, draft, archived or unpublished, or a Collection with no visible Product) is left out of the Storefront. A target cannot be deleted while a Menu item points to it (see [ADR 0005](docs/adr/0005-deletes-are-owner-only-and-refused-while-referenced.md)); the Owner removes the Menu item first.

**Collection**: A named, hand-picked and hand-ordered list of Products shown on the Storefront, such as "Editor's choice" or "Autumn collection". Flat, unlike the Category tree: a Product can be in many Collections or none, and its primary Category is unaffected. Has a name, a short plain-text description and an optional cover image. Shoppers see it on its own page, and it can be a Menu item target. Its Products follow the usual Storefront rules (draft and archived are hidden, sold out are shown as sold out), and a Collection with no visible Products is not shown. Publishing is always a manual act. Deleting a Collection never touches its Products.
- A Collection is **draft**, **pending approval**, **published** or **unpublished** (off the Storefront but kept).
- The Owner can publish a draft directly, unpublish, and publish an unpublished Collection again. Staff create and edit drafts and **submit** them for approval; they cannot publish. The Owner **approves** (which publishes) or **rejects** (which returns it to draft).
- An Owner may edit a pending Collection before approving it; approving publishes the Owner's edited version. Staff may withdraw their own submission back to draft.
- A rejection may carry a reason, shown to the Staff user who submitted it. Any Staff user may edit or submit any draft.
- **Pending change**: A Staff edit to a published Collection. The live version stays on the Storefront until the Owner approves the change. Only the Owner edits a published Collection directly, and only the Owner unpublishes or deletes one. A Collection has at most one pending change (a second Staff edit amends it), and while it exists the Owner approves or rejects it before editing, unpublishing or deleting the Collection (see [ADR 0008](docs/adr/0008-staff-edits-to-published-collections-are-pending-changes.md)).
- The Owner sees how many submissions and pending changes are waiting as a count badge on the Collections item of the Dashboard menu, and finds them with the Collections list's status filter. Every move (create, submit, withdraw, approve, reject, publish, unpublish, delete) and every edit, including a pending change's submit, approve and reject, is written to the Audit log.
- **Home slider Collection**: The Collection with the slug `ana-sayfa` feeds the featured slider on the Storefront home page, with its Products in the Collection's order. While it is missing, unpublished or has no visible Product, the slider shows the newest Products instead. The Owner manages it like any other Collection, and its slug must not be changed or reused for something else. The slug is fixed in the Storefront, not a setting.

**Pre-information snapshot**: The frozen copy of what a Shopper was shown before ordering, with that Order's items, prices, shipping fee and COD fee. Kept with the Order as proof.

**Consent record**: What a Shopper agreed to at checkout (contract acknowledgement, COD fee consent), with the Legal page versions, time, IP address and user agent. Marketing consent is separate and withdrawable.

**Hygiene product**: A Product whose Product class is flagged as not returnable once opened (for example underwear). Shown with a note on the product page and in the cart.

**Store settings**: The single Owner-edited record holding the seller's legal identity (name, tax or MERSIS number, address, contact, designated return carrier, complaint contact).

**Phase 1**: First release. Payments by bank transfer and cash on delivery only.
