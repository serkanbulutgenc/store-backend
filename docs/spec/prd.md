# Aura Tekstil online store: phase 1 PRD

Status: handoff-ready, assembled from the closed tickets of the wayfinding map (`../wayfinder/map.md`). Every section links to the ticket that holds the detail and reasoning; this document states the rules and does not re-argue them. Vocabulary is in `/CONTEXT.md`. The technical design is in [architecture.md](architecture.md).

## 1. Product

An online store for Aura Tekstil, a physical women's clothing, underwear, shoes and accessories shop in Mersin. Turkish-only, TRY, prices shown KDV-inclusive. One **Storefront** for Shoppers and one **Dashboard** for 1-5 Staff (an Owner plus Staff). Stock is shared with the physical shop and adjusted by hand. Phase 1 payments: bank transfer and cash on delivery only. Suppliers and brands are internal attributes; this is not a marketplace.

Source tickets: [map](../wayfinder/map.md) (Destination, Notes).

## 2. Storefront

**Browsing** ([catalog browsing, search, filtering and SEO](../wayfinder/tickets/19-catalog-search-seo.md))
- Header menu of top-level categories with a simple dropdown of children. Homepage: one optional banner (Store setting), "Yeni gelenler", categories.
- Category and search listings: filters colour, size, brand, price range, "only in stock" (on by default); sorts newest, price ascending, price descending. 24 per page with a "daha fazla goster" link. Size and colour filters match Variants that have stock; with both set, one Variant must satisfy both.
- Text search over name, SKU, brand and category name, tolerant of Turkish characters and typos.
- Draft and archived Products return 404. A sold-out published Product stays visible, marked "Tukendi", cannot be added to the basket, and is hidden from listings only while "only in stock" is on. "Son N adet" appears at or below the low-stock threshold ([catalog model](../wayfinder/tickets/05-catalog-model.md)).
- Product page: photo gallery filtered by selected colour (shared photos as fallback), colour and size selectors, KDV-inclusive price and optional old price, stock state, shipping and COD notice, "benzer urunler". Hygiene Products show a non-returnable-once-opened note ([legal](../wayfinder/tickets/13-legal-pages-consent-flow.md)).
- SEO: Turkish transliterated slugs (old slugs 301), canonical rules, noindex on filtered and search pages, product structured data, sitemap and robots.

**Basket and checkout** ([guest checkout and accounts](../wayfinder/tickets/07-guest-and-accounts.md), [legal and consent](../wayfinder/tickets/13-legal-pages-consent-flow.md))
- Guest checkout always available; accounts optional, never forced. Basket survives 14 days via cookie. Baskets never reserve stock.
- One-page checkout. Mandatory: full name, email, phone, shipping address (il, ilce, mahalle, street; postal code optional). Billing = shipping unless another address is ticked. One email field with a typo hint.
- Payment method choice: bank transfer or cash on delivery. COD shows its fee as an explicit line the Shopper consents to.
- Summary shows items, quantities, KDV-inclusive total, shipping fee, COD fee, payment method and a withdrawal summary. Mandatory unticked contract acknowledgement; separate one-line privacy notice under the contact form; separate optional unticked marketing consent. The confirm button states that it creates a payment obligation.
- Flat shipping fee with a free-shipping threshold, both configurable ([order lifecycle](../wayfinder/tickets/06-order-lifecycle-payments.md)).
- Abuse protection: rate limits plus a honeypot; no CAPTCHA.

**After ordering** ([messaging](../wayfinder/tickets/16-transactional-messaging.md))
- Confirmation page and email with order number, bank details (transfer), and a signed order link valid 30 days. Order tracking page takes order number plus the email or phone on the Order, showing status, items, totals, tracking number.
- Emails (Turkish, email only): order placed, bank-transfer reminder at 24 h, auto-cancelled, marked paid, shipped with tracking, cancelled by Staff with reason, return received or refund issued, plus account verification, password reset and email change. No "delivered" email, no marketing sends.

**Accounts**
- Sign-up, plus a "create account" offer on the confirmation page. Email and password only. Email verification mandatory before first login.
- Order history and detail, profile, address book (up to 5, one default), change email (re-verified), change password, account-deletion request, and Favorites (a heart per Product, logged-in only, up to 200, no public count or notifications). No reorder or saved payment details.
- Guest Orders attach to an account when its email is verified and matches.

**Legal pages** (static, versioned Markdown, text supplied by the lawyer): distance sales contract, pre-information form, returns and withdrawal (with the hygiene exception), privacy/KVKK notice, delivery and shipping, payment methods, about and contact. In the footer; checkout links the contract, pre-information form and privacy notice. No analytics, no third-party scripts, no cookie banner in phase 1; the privacy notice lists the strictly necessary cookies.

## 3. Order lifecycle ([order lifecycle](../wayfinder/tickets/06-order-lifecycle-payments.md))

- Two independent statuses. Fulfilment: new, preparing, shipped, delivered, completed, cancelled, return requested, returned. Payment: unpaid, paid, refunded.
- Bank transfer: placed as new/unpaid with stock reserved; 48 h to pay, reminder at 24 h, then auto-cancel and release stock; Staff presses "mark as paid" (amount and date recorded) after seeing the transfer.
- COD: goes straight to preparing; phone mandatory; fee configurable (default 0); recorded paid when Staff marks delivered.
- Stock reserved at placement for both methods, released on cancel, consumed on shipping. Shop sales adjust stock by hand with a reason.
- Cancel is Staff-only before shipping, with a reason. After delivery: return requested, returned, then refunded (amount and method or IBAN recorded). No self-service cancel or return, no partial returns, no automation. Shoppers ask by contacting the shop.
- Shipping: Staff enter carrier name and tracking number by hand.
- Per-Order records kept: Consent record (Legal page versions, time, IP, user agent, contract acknowledgement, COD fee consent), Pre-information snapshot (frozen text with that Order's items, prices and fees), per-line KDV snapshot ([invoicing](../wayfinder/tickets/18-invoicing-wiring.md)). Nothing is hard-deleted.

## 4. Dashboard

Layout and density: compact (13px, monochrome, 32px rows) ([prototype](../wayfinder/tickets/11-ui-prototype.md)). Areas ([roles](../wayfinder/tickets/09-admin-roles-permissions.md)):

- **Home:** new Orders, unpaid transfers near expiry, low stock, shipped Orders without an invoice, invoiced Orders needing cancellation or a credit note, published Products without photos. No charts.
- **Orders:** list and detail; mark paid; cancel with reason; ship with carrier and tracking; deliver; return; refund; sent-message log with resend; invoice record; Order invoice sheet. Staff may edit an Order's shipping address and phone only while new or preparing; Staff may correct the email (resends the order email) and add buyer tax number and company title on request; all audited.
- **Products:** with Variants, prices, status and photos ([catalog model](../wayfinder/tickets/05-catalog-model.md), [media](../wayfinder/tickets/15-media-storage.md)).
- **Stock:** manual adjustments with reason; low-stock list.
- **Catalog data:** Categories, Brands, Suppliers, Colour list.
- **Customers:** read-only.
- **Settings:** shipping fee and threshold, COD fee, KDV rate for shipping and COD lines, low-stock default, bank details, notification recipients, homepage banner, seller identity (name, tax or MERSIS number, address, contact, return carrier, complaint contact).
- **Staff users:** invite by email, deactivate only.
- Out: reports, discounts, CMS pages, label printing, messaging inbox.

**Roles.** Two fixed groups, Owner and Staff, not editable in the UI; every endpoint checks a named permission. Owner-only: Settings, Staff users, Colour list, cost price and Supplier data, archive/unarchive, recording refunds, cancelling paid Orders, account-deletion requests, the accountant export. Staff: everything else including marking paid. Owner needs TOTP 2FA; optional for Staff. Business-event audit log (mark paid, cancel, refund, ship, stock adjustment, price change, user and role change, settings change, login, photo upload and delete, invoice edits, export runs).

**Photos.** Upload from desktop or phone camera, per-file progress, drag to reorder, primary image, colour tag per photo, generated alt text. JPEG, PNG, WebP, up to 15 MB, 12 per Product, 800 px minimum long edge. A Product cannot be published without a photo, and the last photo of a published Product cannot be deleted. No bulk import.

## 5. Catalog rules (summary of [catalog model](../wayfinder/tickets/05-catalog-model.md))

One Product per model with colour x size Variants; fixed size lists per Product class; one shared Owner-managed Colour list; two-level categories; optional Brand; one Supplier per Product with cost price; KDV-inclusive shelf price on the Product (Variant may override), KDV rate from the Product class (10% clothing, footwear, underwear; 20% default; accessories per Product pending accountant); auto-generated editable SKU plus optional barcode; draft, published, archived; low-stock threshold default 2.

## 6. Invoicing ([invoicing](../wayfinder/tickets/18-invoicing-wiring.md))

Staff issue each invoice by hand in the free GIB e-Arsiv portal when the Order ships, using the Order's invoice sheet. The store records invoice number, date and status only and never holds the invoice document. Individuals only. The Owner can export a CSV for a date range for the accountant (no email or phone, audit-logged). This export is the one deliberate exception to the earlier "no export" rule in the roles ticket. Cancelled or returned invoiced Orders are flagged until Staff record the credit note.

## 7. Non-functional requirements

Turkish only (`tr-TR`, `Europe/Istanbul`). Money is computed on the server from decimal strings. Responsive storefront; Dashboard desktop-first except the photo upload screen. Order placement must not depend on email delivery. Availability and backups per [deployment](../wayfinder/tickets/17-deployment-ops.md): an accepted loss window of up to 24 hours of Orders, to be confirmed by the Owner.

## 8. Launch checklist (non-code)

1. Lawyer-approved text loaded for every Legal page, the pre-information form, the withdrawal and hygiene wording, and the privacy notice; lawyer maps each checkout field to a lawful basis and confirms bank-transfer refund handling and the 30-day clock for unpaid transfers.
2. ETBIS registration; VERBIS exemption confirmed; IYS registration before the first marketing send; transfer-abroad review for the VPS, email vendor and error-tracking vendor.
3. Accountant: entity tax regime and e-Arsiv status; method (portal or integrator) and monthly volume; buyer ID placeholder for individuals; portal login method; cash-register treatment of COD; KDV rate on shipping and COD fees; KDV class per accessory category; retention beyond 5 years.
4. Return-carrier agreement and named carrier in the pre-information; retention period for personal data.
5. Email vendor chosen; SPF, DKIM and DMARC set on the shop's domain; Google Search Console sitemap submission; DNS owned by the Owner.
6. One successful backup restore drill before go-live; Owner confirms the 24-hour loss window.
7. Existing shop stock loaded into the Dashboard (by hand or a one-off script; no bulk importer in phase 1).

## 9. Out of scope

Marketplace mode, online card payments, native mobile app, Oscar's own dashboard and templates, and the later-phase list on the map (carrier integration and weight or city rates, self-service returns, POS stock sync, marketing sends and analytics, bulk import, SMS, reorder, CAPTCHA).
