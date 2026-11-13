# Aura Tekstil online store: phase 2

Status: scoped by interview with the Owner on 2026-11-12. Builds on [prd.md](prd.md) and [architecture.md](architecture.md); where this file is silent, phase 1 stands. Tickets 38 to 59 are in `docs/build/README.md`.

## 1. Goals and non-goals

Phase 2 reduces manual work and tidies the Dashboard before and after launch. It adds three features and one Dashboard redesign:

1. **Bulk catalogue import**: load and correct the catalogue from a CSV instead of by hand.
2. **Carrier rates**: shipping priced by destination zone and parcel size, instead of one flat fee.
3. **Self-service returns**: Shoppers cancel and request returns themselves.
4. **Admin shell and tabs**: a sidebar layout with grouped navigation, tabbed Settings and product editor, and the two screens phase 1 left out (Customers, Audit log).

Success means less daily manual work for the Owner and more completed orders. Going live is still gated by the launch checklist (PRD section 8); only the import is a pre-launch need.

**Deferred, each returns as its own effort:** online card payments, SMS, the accountant integrator API, POS stock sync, marketing sends and analytics (IYS, cookie banner, data purge), reorder, CAPTCHA, a live carrier API (labels, tracking pulls).

## 2. Bulk catalogue import

- Dashboard upload screen, **Owner only**, one CSV file per run. Columns follow `docs/launch/stock-template.csv`; ticket 39 settles the final set (it adds `product_class` and an optional `sku`, and clarifies `kdv_class`). No photos in the file: photos stay on the existing photo screen.
- **Dry run first.** Upload returns a preview: rows to create, rows to update, per-row errors, and the brands, suppliers and categories that would be created. Nothing is written until the Owner confirms.
- **Create or update.** A variant matches an existing one by `sku` when given, otherwise by product name + colour + size. Rows with the same product name and category become variants of one Product. Existing variants have price and stock updated; stock goes through `stock.adjust` so it is audited.
- Unknown colours, sizes not in the product class's scheme, or bad prices are row errors (a colour needs a swatch, so it is never auto-created). Missing brands, suppliers and categories are created on confirm. Products are created as drafts.
- One audit entry per run (who, file name, counts). The whole confirm step runs in one transaction: all rows or none.

## 3. Admin shell, tabs and missing screens

- **Prototype first**, shown to the Owner before the real build (ticket 41).
- New shell: shadcn collapsible sidebar, navigation grouped as **Satış** (Ana sayfa, Siparişler, Müşteriler), **Katalog** (Ürünler, Stok, Kategoriler, Markalar, Tedarikçiler, Renkler), **Yönetim** (Ayarlar, Personel, Muhasebe dışa aktarımı, Denetim günlüğü, Hesap silme talepleri, Güvenlik). Breadcrumbs, a mobile slide-in menu, user menu and sign-out at the bottom. Permission gating stays as today (items hidden; the API enforces). Mostly desktop use: scrollable tables on small screens, no per-screen responsive redesign.
- **Tabs** on Settings (Kargo ve ücretler, Banka bilgileri, Bildirimler, Ana sayfa afişi, Satıcı bilgileri; one Kaydet writes everything) and on the product editor (Genel, Fiyat, Varyantlar, Fotoğraflar). The order detail page is unchanged.
- **Phase 1 gap:** the sidebar links to `/admin/customers` and `/admin/audit-log`, but neither screen exists and the backend has no admin endpoint for either list (ticket 28 was closed without them). Phase 2 adds the endpoints (ticket 45) and both screens: Customers read-only (Staff with `view_customers`), Audit log Owner-only.

## 4. Carrier rates

- **Carriers:** Yurtiçi Kargo, Aras Kargo, PTT Kargo.
- **Zones:** the 81 provinces map to four zones measured from Mersin (the Owner can edit the mapping).
- **Size is in desi**, as Turkish carriers price it. Each Product class has a default desi; a Product can override it. An Order's desi is the sum over its lines of desi x quantity.
- **Rate table:** per carrier, per zone, price for the bands 0 to 2, 3 to 5 and 6 to 10 desi, plus a price per extra desi above 10. Prices are KDV-inclusive TRY.
- **Checkout** shows one shipping fee: the **cheapest** carrier for the destination province and the Order's desi. The existing **free-shipping threshold** still applies on top (free when the items total reaches it). If the province or a desi is unknown, the flat `shipping_fee` in Settings is the fallback. The fee line keeps its KDV snapshot as today.
- **Staff choose the carrier** when marking an Order shipped, from the three; the name is stored as now. The fee the Shopper paid is not recalculated.
- The Owner edits zones, desi values and the rate table in the Dashboard.
- The seed data is a **dummy sheet** (appendix). Real contract prices replace it before launch (ticket 53).

## 5. Self-service returns

- **Before shipping:** a Shopper cancels an Order that is new or preparing from the tracking page or the account area. Stock is released automatically. If it was already paid, the money waits for a refund recorded by the Owner, as today.
- **After delivery:** the Shopper requests a return from the same pages, choosing lines and quantities, within **14 days of delivery**. Lines of hygiene Product classes (`is_hygiene`) cannot be selected. Reasons: wrong size, changed mind, defective, wrong item sent, other.
- **Approval:** Staff approve or reject each request (a rejection needs a reason). **Return shipping** is paid by the shop for "defective" and "wrong item sent", otherwise by the Shopper; the approval email says which.
- **Refunds stay manual**, recorded by the Owner as in phase 1 (cash or IBAN transfer); there is no card payment to reverse.
- **Emails** (Turkish, through the outbox): request received (Shopper and Staff), approved or rejected, items received, refund sent.
- **Records:** a return request is its own record with its own status (requested, approved, rejected, received, refunded), not just an Order status, so a partial return does not mark the whole Order returned. Ticket 55 reconciles it with the phase 1 Order-level return statuses and the `RULES` table in `services/order_ops.py`.
- The return wording and the return-carrier agreement need lawyer or Owner approval before go-live (ticket 59, launch checklist 4a and 1b).

## 6. Architecture groundwork

Done at the start of phase 2 (commits `3168cbf`, `2016d66`, `c38df14` in store-backend): one rule table and transition helper for the order lifecycle (`order_ops.RULES`), and `services/stock.py` owning stock arithmetic, record lookup and reservations.

Each remaining cleanup rides with the feature that touches the same code:

| Cleanup | Ticket | Why there |
|---|---|---|
| Catalog API view-models and owner-only field policy into services | 38 | the import calls the same catalog services |
| Checkout split into `quote` and `place` | 48 | shipping fees are computed in checkout |
| Outbox transport seam (SMTP and in-memory adapters) | 54 | returns add many emails |
| Media storage seam | not planned | only if a feature needs it |

## 7. Sequencing and release

Order: import (38 to 40), admin shell and missing screens (41 to 47), carrier rates (48 to 53), returns (54 to 59). Slices ship independently with `scripts/deploy.sh`; each backend change is backwards-compatible for one deploy, as in phase 1. Every ticket keeps its tests green; the full suite runs at the end of each.

## 8. Launch checklist additions

- Real carrier contract prices loaded (replaces the dummy sheet).
- Return wording and return-carrier terms approved (adds to checklist 1b and 4a).
- `docs/launch/stock-load.md` updated: the import becomes the stock-load method.

## 9. Open items

- **Real prices** for Yurtiçi, Aras and PTT, and the desi value per Product class.
- **Restocking:** whether returned items go back on the shelf. Phase 1 `mark_returned` does not touch stock. Default for ticket 56 unless the Owner says otherwise: Staff choose per returned line.
- **Return-carrier agreement** (checklist 4a) is not signed.

## Appendix: dummy rate sheet (placeholder numbers, not quotes)

Zones: **1** Mersin. **2** Adana, Hatay, Osmaniye, Karaman, Niğde, Konya, Antalya, Gaziantep, Kahramanmaraş. **3** the rest of Central Anatolia, the Aegean, the Mediterranean and Marmara (including İstanbul, Ankara, İzmir). **4** the Black Sea, Eastern and Southeastern Anatolia. Prices in TRY, KDV-inclusive.

| Carrier | Zone | 0 to 2 desi | 3 to 5 desi | 6 to 10 desi | each extra desi over 10 |
|---|---|---|---|---|---|
| Yurtiçi | 1 | 55 | 65 | 80 | 6 |
| Yurtiçi | 2 | 70 | 80 | 100 | 8 |
| Yurtiçi | 3 | 85 | 100 | 125 | 10 |
| Yurtiçi | 4 | 100 | 120 | 150 | 12 |
| Aras | 1 | 52 | 62 | 76 | 6 |
| Aras | 2 | 66 | 76 | 95 | 8 |
| Aras | 3 | 80 | 95 | 118 | 10 |
| Aras | 4 | 95 | 114 | 142 | 11 |
| PTT | 1 | 45 | 55 | 70 | 5 |
| PTT | 2 | 55 | 65 | 85 | 7 |
| PTT | 3 | 65 | 80 | 105 | 8 |
| PTT | 4 | 75 | 95 | 125 | 10 |
