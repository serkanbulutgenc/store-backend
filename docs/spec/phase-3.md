# Aura Tekstil online store: phase 3

Status: scoped with the Owner on 2026-11-20 through the wayfinder map "Map: phase 3 conversion features spec" (store-infra issues #58 to #64). Builds on [prd.md](prd.md), [architecture.md](architecture.md) and [phase-2.md](phase-2.md); where this file is silent, those stand. Vocabulary is in `GLOSSARY.md` (terms added here: Stock alert, Size guide, Fit note, Similar products, Recently viewed, Report).

## 1. Goals and non-goals

Phase 3 raises conversion and cuts size-related returns without adding visitor tracking. Four features:

1. **Stock alerts**: a Shopper asks to be emailed once when a sold-out Variant returns.
2. **Size guide and Fit note**: measuring reference and a one-line fit comment on the product page.
3. **Similar products and Recently viewed**: a ranked similar rail and a device-local viewed list.
4. **Reports**: an Owner-only screen of sales, Products, returns, and Campaign and Voucher figures.

**Not in phase 3, each returns as its own effort:** online card payments (iyzico), abandoned-basket reminders and any other marketing send, POS stock sync, SMS, a live carrier API, reorder, CAPTCHA, page-view or funnel analytics.

## 2. Stock alerts

Source: [Grill: back-in-stock alerts](https://github.com/serkanbulutgenc/store-infra/issues/60); legal basis in [Research: back-in-stock emails under Turkish law](https://github.com/serkanbulutgenc/store-infra/issues/59) (`docs/research/back-in-stock-law.md` on the store-infra branch `research/back-in-stock-law`).

- **Gate.** Treated as a commercial electronic message (ETK). A Store setting keeps the feature off until the Owner confirms İYS registration. It is not a go-live requirement.
- **Scope.** One Stock alert per sold-out Variant. The size picker shows "Haber ver" on a sold-out Size.
- **Subscribing.** Guests (email) and accounts. An unticked, specific consent box with the privacy notice linked. A guest confirms by email: the click is the consent record. A signed-in Shopper's verified email skips the confirm step. The checkout marketing flag is not reused.
- **Firing.** Once, when a Variant of a published Product goes from 0 available to 1 or more, whatever the cause (adjustment, import, cancellation release, restocked return). Everyone waiting is emailed through the existing outbox, first come first served, and each live row is then deleted. Alerts lapse after 90 days.
- **Records.** A live **Stock alert** row (email, Variant, confirmed time, expiry) and a separate **Alert consent record** (email, Variant, consent time, notice version, how given, reject time) kept 3 years, then purged. A reject link in every email deletes the live row and stamps the reject time. İYS entries are made by hand by the Owner. Account deletion or anonymisation deletes live Stock alerts.
- **Email.** Product, Colour, Size, current price (Campaign price if one applies), one button to the product page, a "limited stock" line, the reject link. No cross-selling.
- **Abuse limits.** Per-IP rate limit on subscribing; one per email and Variant (confirm mail resent at most hourly); at most 5 active per email; unconfirmed rows purged after 48 hours; a confirm email only while the Variant is sold out.
- **Dashboard.** A "waiting" count (confirmed only) in the stock grid for Owner and Staff. Emails are not shown.

## 3. Size guide and Fit note

Source: [Grill: size guide](https://github.com/serkanbulutgenc/store-infra/issues/61).

- **Size guide.** One per Product class, with an optional override on a single Product. A Product with neither shows no link. Removing an override returns the Product to its class guide; editing a class guide changes every Product using it at once, with no versioning.
- **Content.** Formatted text through the existing Description editor and renderer (tables, images, tips); images reuse the Description image upload.
- **Display.** A "Beden tablosu" link beside the size picker opens a dialog (a bottom sheet on phones), on the product page and in quick view. Hidden for a One-size Product or when no guide applies. The dialog loads the guide when opened, so guide text stays out of product search and JSON-LD.
- **Editing.** Owner and Staff, no approval flow; edits go in the Audit log.
- **Fit note.** Optional one-line plain-text note per Product ("Kalıp notu"), shown under the size picker and in quick view and present in the page HTML. Owner and Staff edit it.
- **Import.** The CSV import gains an optional fit-note column (empty means no change). Guides are not imported.

## 4. Similar products and Recently viewed

Source: [Grill: related products and recently viewed](https://github.com/serkanbulutgenc/store-infra/issues/62).

- **Similar products.** Replaces today's "same leaf Category, first 4 of the listing". Ranked by same leaf Category, then same Brand, shared attribute values, and a small bonus for a price within ±30%; deterministic, popularity as the tiebreak; no favorites or order co-occurrence. Published, in-stock Products only, the current one excluded. Fewer than 4: widen to the parent Category; still none: hide the block. A rail of up to 8 reusing the homepage rail component, below the tabs, not inside quick view.
- **API.** New store endpoint `/products/{slug}/similar`, ranking in the browse service, short cache. Server-rendered for internal links.
- **Recently viewed.** Up to 12 Product slugs in the browser's local storage; no server log, no cross-device sync. A batch endpoint takes the slugs (max 12) and returns fresh card data. A "Son baktıklarınız" rail on the homepage (above "Yeni gelenler") and on the product page (below Benzer ürünler), shown only when there is history; current Product excluded; sold-out and unpublished Products drop out. "Temizle" clears it, and sign-out clears it. Client-only after hydration.
- **Privacy.** One sentence in the privacy notice (strictly functional, kept on the device, clearable). No banner.

## 5. Reports

Source: [Grill: Owner sales analytics](https://github.com/serkanbulutgenc/store-infra/issues/63).

- **Where.** A separate Owner-only "Raporlar" screen; Dashboard Home is unchanged. Fixed Owner-only, no grantable permission; Staff get no menu entry and a 403 from the API.
- **Reports.** (1) **Sales** by day, week or month: orders, items, revenue, average order. (2) **Products** ranked by units and revenue. (3) **Returns** by Product and Size: reason mix and rate. (4) **Campaigns and Vouchers**: orders, discount given, revenue. The waiting-alert count stays in the stock grid.
- **What counts.** Cancelled orders excluded; returned lines netted out; unpaid bank transfers inside the 48-hour window shown as pending, apart from confirmed sales. KDV-inclusive with a KDV-exclusive toggle; shipping and COD fees shown separately. Revenue is what the Shopper paid, net of the Voucher share; the Voucher discount is its own column in report 4.
- **Ranges.** Presets (today, 7 days, 30 days, this month, last month) and a custom range; default 30 days, compared with the previous period; days in Europe/Istanbul.
- **Return rate.** Returned units over units shipped, by order date, by Product and by Size. Rows under 5 units show counts only. Judging the Size guide is the Owner's own period comparison, not an automatic feature.
- **Computation.** Live queries over orders and order lines behind one reports service with a short cache; no summary tables. That service is the seam to swap if it gets slow.
- **Export.** CSV per report, Owner only, written to the Audit log. No PDF, no scheduled email.
- **No tracking.** No page-view, funnel or conversion data.

## 6. Cross-feature reconciliation

- **One stock-change hook.** Stock alerts fire on a 0 to 1-or-more transition and the stock grid shows the waiting count, so both read stock through `store_backend/services/stock.py`. The hook belongs in that module's mutating paths (`release`, `restock`, `adjust`, `adjust_many`, and with them the import, which already goes through `stock.adjust`), not in a database signal. It enqueues work after commit.
- **Description editor reuse.** The Size guide uses the Description editor, renderer and image upload. The Description's length cap and sanitising apply to guides too; check the cap suits a measuring table before building.
- **Price in alerts and rails.** The alert email and the Similar and Recently viewed rails show the Campaign price when one applies. All three use the existing computed price (ADR 0009), never a stored one.
- **Reports reads existing data.** Returns by Size needs the Size on each returned line, and revenue net of Voucher reuses the refund-net-of-Voucher rule from the returns work. No new capture is needed if both are already stored on order lines.
- **Outbox.** Stock alert mail and the confirm mail use the existing outbox, so failures stay visible and retried.
- **Glossary and ADRs.** The six new terms are in `GLOSSARY.md`. No ADR is proposed: nothing here is hard to reverse and surprising at once.

## 7. Sequencing and release

Slices ship independently with `scripts/deploy.sh`; each backend change is backwards-compatible for one deploy, and the backend goes live before the frontend that calls it. Suggested order, cheapest value first:

1. **Size guide and Fit note**: no legal dependency. Backend (class guide, Product override, fit note field, import column), then Dashboard editors, then the storefront dialog and fit line.
2. **Similar products**: backend ranking endpoint, then the rail.
3. **Recently viewed**: batch endpoint, then the device-local list, sign-out clearing, rails, and the privacy sentence.
4. **Reports**: reports service and API, then the screen and CSV.
5. **Stock alerts**: last, because it waits for İYS registration and lawyer-approved text. Build behind the Store setting; sub-slices are the data model and consent records, the stock-change hook, the subscribe and confirm flow, the alert email and reject link, and the stock grid count.

## 8. Launch checklist additions

- İYS registration (before turning Stock alerts on).
- Lawyer-approved Turkish consent wording and Stock alert privacy notice, plus the one privacy-notice sentence for Recently viewed.
- An Owner runbook step for recording consent in İYS by hand, and for the 3-day İYS and 24-hour confirmation timings.
- Add each item to `docs/launch/checklist.md` in store-infra when the phase is scheduled.

## 9. Unverified assumptions (build-time checks)

- The legal reading (commercial message) rests on an inference; no source addresses back-in-stock alerts directly. The research read the Regulation only to its 4 January 2020 consolidation, could not open the İYS FAQ, and cites several KVKK Board decisions from search summaries. A lawyer confirms before the setting is switched on.
- Every path that raises available stock goes through `stock.py`. Confirm that cancellation release and the returns restock do not write Oscar stock records directly.
- Order lines keep the Size and the Voucher share needed for report 3 and for revenue net of Voucher.
- The Description length cap fits a size table.
- Small-seller İYS registration and VERBİS applicability, and the cross-border email transfer question, are for the Owner's lawyer.

## 10. Open items

- Whether Stock alerts ship before or after launch depends on İYS registration timing; the Owner decides.
- Whether a trusted Staff user should ever see Reports is a new decision, not covered here.
