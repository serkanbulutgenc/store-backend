---
title: Grill: invoicing workflow, order data for e-Arsiv and accountant export
label: wayfinder:grilling
status: closed
assignee: claude
blocked-by: [14-research-earsiv-invoicing.md]
parent: map.md
---

## Question

Given research/14-earsiv-invoicing.md, decide how phase 1 produces invoices: manual issuing in the GIB portal versus an integrator API versus direct integration (pending accountant input on the entity and method); what each Order must carry so an invoice can be issued (the internet-sale fields, buyer tax ID or placeholder, KDV breakdown per rate, payment method and date, carrier and dispatch date); what the Dashboard shows (invoice number/status per Order, "invoice issued" marker, cancellation within 8 days); the accountant export (fields, format, who can run it, reconciling with the earlier "no export" rule for personal data); how invoice data is retained (5 years, inside Turkey) and reconciled with the no-hard-delete and anonymisation rules; and what is handed to the accountant as a launch blocker.

## Resolution

Decided in a two-round grilling; all recommendations accepted. Brief for the accountant: [research/14-earsiv-invoicing.md](../research/14-earsiv-invoicing.md).

**Method**
- Phase 1 is manual: Staff issue each invoice in the free GIB portal, using a copy-ready invoice sheet per Order in the Dashboard. The store never creates or stores the invoice document, so the 5-year retention inside Turkey sits with the portal or accountant, not the EU VPS.
- The Order keeps an invoice record (number, date, status) that an integrator API could fill later. Switching to a licensed integrator is a later ticket if the accountant finds manual issuing unsuitable for the volume. "Accountant confirms the method and monthly volume" is the single blocking launch item for this choice.

**Order data**
- At placement each line snapshots: quantity, KDV-inclusive unit price, KDV rate, exclusive amount and KDV amount, rounded to kuruş per line, with order totals as the sum of lines. Shipping fee and COD fee are separate lines with a KDV rate set in Store settings (default 20%, Owner and accountant confirm). The snapshot never changes when Product prices or rates change. No discounts in phase 1.
- Individuals only: the invoice uses the Order's name and billing address and a buyer ID the accountant confirms (a placeholder setting, expected `11111111111`, unconfirmed). No corporate fields at checkout; for a company invoice the Shopper contacts the shop and Staff or Owner add buyer tax number and company title on Order detail (audit-logged, visible only there).

**Workflow**
- The invoice is issued when the Order is marked shipped (carrier, tracking and dispatch date known). Order detail records invoice number, date and status (`not issued`, `issued`, `cancelled`) by hand (audit-logged); Dashboard Home lists "shipped without an invoice".
- The store does not issue credit notes. When an invoiced Order is cancelled or returned, it gets an "invoice needs cancelling or crediting" flag, listed on Home, cleared by Staff or Owner who record the credit note number and date. Order detail shows the 8-day cancellation cut-off after delivery as a reminder.

**Export and retention**
- One narrow export, amending the earlier "no export" rule: a CSV for a chosen date range, Owner only, audit-logged (range and user). Columns: order number, order and ship dates, buyer name and billing address, lines (description, quantity, KDV rate, exclusive amount, KDV amount), shipping and COD lines, total including KDV, payment method and date, carrier and dispatch date, invoice number and status. Email, phone and notes are excluded. Staff cannot export but can view the single-Order invoice sheet.
- Orders keep their snapshot name and billing address for the legal retention period even after the account is anonymised (invoice data, not profile data); the privacy notice says so (lawyer checklist item). Only the account-linked profile and optional extras are anonymised.

**Launch checklist additions (accountant):** entity tax regime and e-Arsiv status; method and volume; buyer ID placeholder; portal login method; cash-register treatment of COD; KDV rate on shipping and COD fees; accessory KDV classes; retention beyond 5 years.
