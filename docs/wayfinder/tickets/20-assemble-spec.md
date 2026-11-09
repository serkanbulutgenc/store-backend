---
title: Task: assemble the phase 1 PRD and architecture spec
label: wayfinder:task
status: closed
assignee: claude
blocked-by: []
parent: map.md
---

## Question

Assemble the destination artifact from the 19 closed tickets: a handoff-ready PRD plus architecture spec for phase 1 (storefront and Dashboard), so implementation agents can slice it into build tickets for store-backend, store-frontend and store-infra. No new decisions: where tickets conflict or amend each other (for example the Owner-only invoice export versus "no export"), state the final rule; list unverified items (allauth token middleware, HEIC conversion, Start cookie forwarding) as build-time checks; gather the launch checklist spread across the tickets into one non-code section; link each section back to its source ticket instead of restating detail.

## Resolution

Spec assembled as two documents, as chosen: [prd.md](../../spec/prd.md) (product rules, Storefront, order lifecycle, Dashboard, invoicing, non-functional requirements, launch checklist, out of scope) and [architecture.md](../../spec/architecture.md) (repos and runtime, backend, frontend, delivery and operations, privacy, six build-time checks, suggested build slices). Both link back to the source tickets. No new decisions were made.

Cross-ticket reconciliations stated in the spec: the Owner-only invoice CSV export is the one exception to "no export" in the roles ticket; Order detail gained email correction, buyer tax number and company title, invoice record and message log; Settings gained seller identity, notification recipients, KDV rate for shipping and COD lines, and the homepage banner; Home gained three lists (shipped without invoice, invoice needing credit note, published without photos); the hygiene flag lives on the Product class; marketing consent is stored but SMS is out of scope.
