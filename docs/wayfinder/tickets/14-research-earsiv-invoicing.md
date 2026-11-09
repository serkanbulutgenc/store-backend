---
title: Research: e-Arsiv invoicing options and technical requirements
label: wayfinder:research
status: closed
assignee: claude
blocked-by: []
parent: map.md
---

## Question

Establish facts from primary sources (GIB ebelge.gib.gov.tr, integrator docs): how a small online seller issues e-Arsiv Fatura (GIB portal vs licensed integrator vs own integration), what each costs and requires, the invoice data fields and e-Arsiv report submission, issuing to buyers without a tax ID (TCKN placeholder), whether an API exists for automating issuance from an order, what a CSV/export-based accountant workflow needs, and invoice retention. Write findings to research/14-earsiv-invoicing.md with source links; flag items needing the accountant.

## Resolution

Findings in [research/14-earsiv-invoicing.md](../research/14-earsiv-invoicing.md). Summary: GIB names three issuing methods (free GIB Portal, licensed ozel entegrator, direct integration with own system). Direct integration needs UBL-TR signed with mali muhur/NES, a signed daily e-Arsiv report over a SOAP web service, and GIB test approval. Integrators (e.g. Nilvera, Parasut) expose REST APIs priced per document; prices unverified. The portal is manual with no bulk or API path found. Internet sales need extra invoice fields (web address, payment method and date, carrier and VKN, dispatch date, return section, fixed sentence). Cancellation is within 8 days of delivery. Retention is 5 years (VUK 253/254). Open items for the accountant: entity status and method, current portal login and limits, TCKN placeholder (11111111111, not in a primary source), COD receipt rules, returns after 8 days, retention beyond 5 years.
