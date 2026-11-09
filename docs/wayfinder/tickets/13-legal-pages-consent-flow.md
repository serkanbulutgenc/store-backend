---
title: Grill: legal pages, checkout consent flow and record keeping
label: wayfinder:grilling
status: closed
assignee: claude
blocked-by: []
parent: map.md
---

## Question

Decide what the store must show and record to meet distance-selling and KVKK rules (research 04) without owning the legal wording: the set of legal pages and who edits them (static vs staff-editable), what the pre-information re-display and confirmation look like at checkout (before payment, per payment method), how consent is captured and stored (contract acknowledgement, marketing consent, COD fee consent) and kept 3 years, how the withdrawal/return policy and the underwear exception are presented, the seller identity data (MERSIS/tax ID, address, return carrier) and where it is configured, and what is explicitly handed to the lawyer as a launch blocker.

## Resolution

Decided in a two-round grilling; all recommendations accepted. Vocabulary added to `CONTEXT.md`: Legal page, Pre-information snapshot, Consent record, Hygiene product, Store settings.

**Wording and ownership**
- The spec defines pages, mechanism and data; the lawyer's text is loaded as content later. "Lawyer-approved text loaded" is a launch blocker, not a build blocker.
- Legal pages are static Markdown in the frontend repo, changed by deploy. Each has a version identifier.
- Pages: Mesafeli Satis Sozlesmesi, On Bilgilendirme Formu, Iade ve Cayma Kosullari (incl. hygiene exception), Gizlilik ve KVKK Aydinlatma Metni, Teslimat ve Kargo, Odeme Yontemleri, Hakkimizda / Iletisim. Footer on every page; checkout links contract, pre-information form and privacy notice.
- Seller identity (name, tax/MERSIS number, address, phone, email, designated return carrier, complaint contact) lives in one Owner-edited Store settings record, audit-logged, reused later for invoices.

**Checkout**
- One step, no separate review page. The order summary shows items, quantity, KDV-inclusive total, shipping fee, COD fee, payment method and a withdrawal summary; the confirm button label states that it creates a payment obligation.
- Contract acknowledgement checkbox (unticked, mandatory) is separate from the privacy notice, which is a one-line inline notice with a link under the contact form (notice and consent never bundled). Marketing consent is a separate unticked optional box.
- Hygiene exception: boolean on Product class; flagged products show a note on the product page and a line in the cart summary.

**Proof and retention**
- Each Order stores a Consent record (Legal page versions, timestamp, IP, user agent, contract acknowledgement, COD fee consent if COD) and an immutable Pre-information snapshot rendered with that Order's items, prices and fees. Marketing consent is stored on the Shopper/contact with its own timestamp.
- Nothing is hard-deleted in phase 1. Retention period (3 years minimum; invoices longer) is a lawyer/accountant question; a purge job is a later ticket if they set a period.

**Cookies, marketing, data requests**
- No analytics, pixels or third-party scripts in phase 1, so no cookie banner; the privacy notice lists the strictly necessary cookies. Adding analytics later needs its own ticket.
- Marketing consent is captured and stored; a Customer can toggle it in the account. No marketing is sent in phase 1. Transactional order messages need no marketing consent.
- KVKK requests (access, correction, deletion) come by email to the Store settings contact address; the Owner answers within 30 days. Account deletion anonymises the profile and keeps Orders with their Consent record and invoice data for the retention period. No self-service form.

**Launch checklist (non-code), to appear in the final spec:** lawyer-approved texts loaded; ETBIS registration; VERBIS exemption confirmed; IYS registration before the first marketing send; accountant sign-off on KDV rates and e-Arsiv; return-carrier agreement; retention period set.
