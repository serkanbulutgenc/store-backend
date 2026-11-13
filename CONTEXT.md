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
- **Destructive action**: An action that sends money out or removes something for good: archiving a Product, recording a refund, cancelling a paid Order, handling an account deletion request. Owner-only. Mistakes that can be corrected by editing, such as a wrong stock count or price, are not destructive; the Audit log is their safety net. Marking an Order paid only records money that has arrived, so Staff may do it, though Staff cannot undo it.
- **Invitation**: An Owner's email invite through which a new Dashboard user sets a password. Dashboard users are deactivated, never deleted.

**Audit log**: Append-only record of sensitive Dashboard actions (who, when, what, before and after), shown on the Order and as an Owner-only list.

**Product**: A sellable model as shoppers see it (e.g. a dress), which comes in several **Variants** (colour x size). Belongs to one primary **Category** and one **Product class**. Is draft, published or archived.

**Variant**: One purchasable colour/size combination of a Product, with its own stock, **SKU** and optional barcode.

**Product class**: A kind of Product (clothing, footwear, underwear, accessory) that fixes its size scheme, default KDV rate, hygiene flag and default desi. The Owner creates, edits and deletes classes; a class with Products cannot be deleted and its size scheme is locked. A changed default KDV rate applies to Products created afterwards only.

**Size scheme**: The fixed list of sizes allowed for a Product class (letters, numbers, shoe sizes, bra sizes, one-size).

**Attribute**: A descriptive property of a Product class (for example Kumaş or Sezon), defined by the Owner. It is a text field or an option list; an option list holds one value or several, fixed when the attribute is created, and may be marked filterable for the Storefront. Options belong to one attribute and are never shared. Attributes are optional: adding one backfills nothing and an empty value is not shown.

**Attribute value**: What a Product holds for one Attribute: a text, or the chosen option or options. Staff fill them in on the Product. Deleting an Attribute or an option that Products use removes those values, which is a Destructive action: the Dashboard says how many Products are affected and the API requires confirmation.

**Colour**: An entry in the Owner-managed shared colour list, with a swatch.

**Category**: A node in a two-level tree (e.g. Giyim > Elbise). A Product has one primary Category.

**Brand**: The maker label of a Product (e.g. a clothing label). Optional.

**Supplier**: A company Aura Tekstil buys stock from. Internal only; Suppliers never sell on the Storefront. A Product has one Supplier, with a supplier code and cost price.

**SKU**: Unique, auto-generated (editable) code identifying a Variant.

**Shelf price**: The KDV-inclusive price shown to Shoppers. Set on the Product; a Variant may override it.

**Old price**: Optional earlier shelf price shown struck through to signal a discount.

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

**Claiming**: Attaching a Guest's Orders to a Customer account once the account's email is verified and matches the Order's email.

**Legal page**: A fixed public page carrying contract, policy or notice text supplied by the lawyer (distance sales contract, pre-information form, returns and withdrawal, privacy notice, delivery, payment methods, about and contact). The Owner edits it in the Dashboard in Markdown. Shoppers can search the Legal pages.
- **Legal page version**: One saved text of a Legal page. Every save creates a new version; a version never changes afterwards, so a Consent record always points to the exact text a Shopper accepted.

**Pre-information snapshot**: The frozen copy of what a Shopper was shown before ordering, with that Order's items, prices, shipping fee and COD fee. Kept with the Order as proof.

**Consent record**: What a Shopper agreed to at checkout (contract acknowledgement, COD fee consent), with the Legal page versions, time, IP address and user agent. Marketing consent is separate and withdrawable.

**Hygiene product**: A Product whose Product class is flagged as not returnable once opened (for example underwear). Shown with a note on the product page and in the cart.

**Store settings**: The single Owner-edited record holding the seller's legal identity (name, tax or MERSIS number, address, contact, designated return carrier, complaint contact).

**Phase 1**: First release. Payments by bank transfer and cash on delivery only.
