---
title: Aura Tekstil online store - wayfinding map
label: wayfinder:map
---

## Destination

A handoff-ready PRD + architecture spec for **phase 1** of the Aura Tekstil online store (storefront AND staff/admin dashboard), plus the locked decisions behind it, so implementation agents can slice it into build tickets for `store-backend` and `store-frontend`. Scaffolding and building are NOT part of this map.

## Notes

- Business: physical women's clothing/underwear/shoes/accessories shop in Mersin, Turkey. Turkish-only UI, TRY, KDV shown inclusive. 1-5 staff (owner + staff roles). ~hundreds-thousands of SKUs with size/colour variants. Inventory shared with the physical shop.
- Stack (fixed): cookiecutter-django, django-oscar (commerce engine, its own dashboard NOT used), django-ninja (REST), django-allauth (auth). Frontend: TanStack ecosystem (Start/Router/Query/Table/Form) + shadcn/ui, compact design. Two separate repos: `/home/sbulutgenc/Projects/Python/store-backend`, `/home/sbulutgenc/Projects/Web/store-frontend`.
- Phase 1 payments: bank transfer + cash on delivery only. Guest checkout required; optional email/password signup.
- "Multi supplier and brand" = internal sourcing attributes, NOT a marketplace.
- Frontend = one TanStack Start app: storefront at `/`, dashboard under `/admin`.
- Hosting assumption: single VPS, Docker Compose.
- Domain vocabulary lives in `/CONTEXT.md` at the backend repo root; consult it. Use the grilling + domain-modeling skills for grilling tickets.

## Decisions so far

<!-- one line per closed ticket -->

- [Research: django-oscar fit for a headless Turkish store](tickets/01-research-oscar-fit.md): Oscar 4.2.1 supports Django 5.2/6.0; models/OrderCreator work headless but checkout views and basket middleware are required; stored price is tax-exclusive (KDV-inclusive needs a custom pricing policy); no Brand model (Partner is a fulfilment partner); offline payments are custom; four items unverified (see spike).
- [Research: allauth + Ninja auth for an SSR TanStack Start frontend](tickets/02-research-headless-auth.md): allauth headless has browser (session+CSRF) and app (X-Session-Token) clients; Oscar's guest basket is its own signed cookie; guest checkout settings default off; SSR cookie forwarding is undocumented.
- [Research: TanStack Start maturity and admin-dashboard building blocks](tickets/03-research-tanstack-start.md): Start is stable (RSC experimental), Node deploy via Nitro (no official Docker example); ninja emits OpenAPI 3.1 for client generation; shadcn data-table on Table v9, TanStack Form has a shadcn guide; server-side table paging unconfirmed.
- [Research: Turkish e-commerce tax and legal rules](tickets/04-research-turkey-commerce-rules.md): clothing/footwear/underwear at 10% KDV, general 20%, accessories need accountant classification; prices KDV-inclusive; distance-selling rules (14-day withdrawal, underwear exception, pre-payment info); e-Arsiv likely required for online sales; several items need an accountant or lawyer.
- [Grill: order lifecycle, payment methods and stock handling](tickets/06-order-lifecycle-payments.md): separate fulfilment and payment statuses; bank transfer 48 h hold with manual mark-as-paid; COD straight to preparing with configurable fee and mandatory phone; stock reserved at placement, adjusted manually for shop sales; staff-only cancel and return states; flat shipping fee with manual tracking.
- [Grill: guest checkout, optional accounts and merging](tickets/07-guest-and-accounts.md): guests give name, email, phone and address; tracking by signed link or order number plus email; cookie basket kept 14 days; optional accounts with mandatory email verification; guest orders attach to a verified account by email; address book up to 5; rate limits plus honeypot, no CAPTCHA.
- [Spike: Oscar headless viability and SSR cookie forwarding](tickets/12-spike-headless-oscar-and-ssr-cookies.md): Oscar 4.2.1 migrates on the cookiecutter email User (Postgres required) and runs without its dashboard apps via a no-op `DashboardConfig` shim; session cookies + CSRF + the guest basket cookie survive an SSR forwarder (Start's own API still to check); Ninja `django_auth` enforces CSRF, `auth=None` endpoints do not, and `X-Session-Token` puts the user in `request.auth` so Oscar ignores it.
- [Grill: catalog model](tickets/05-catalog-model.md): one Product per model with colour x size Variants; fixed size/colour lists; two-level categories; optional custom Brand; one Supplier per Product; KDV-inclusive shelf price with rate from Product class; auto SKU plus barcode; draft/published/archived; low-stock threshold 2.
- [Grill: API boundary between Ninja and Oscar](tickets/08-api-boundary.md): service layer over Oscar; two Ninja APIs (store, admin); allauth app mode with a token-to-request.user middleware (unverified, falls back to browser mode); guest endpoints keep explicit CSRF; offset paging, one error envelope, committed OpenAPI specs with openapi-typescript/openapi-fetch, no versioning, decimal-string money.
- [Grill: dashboard scope, roles and permissions](tickets/09-admin-roles-permissions.md): eight-area Dashboard; fixed Owner and Staff groups with named permissions checked per endpoint; cost price omitted for Staff; business-event audit log; invite-only Staff with Owner 2FA; no export or impersonation.
- [Grill: frontend architecture and app structure](tickets/10-frontend-architecture.md): private Django behind a Start proxy route; storefront SSR, admin client-only; Turkish storefront and English `/admin` paths; Query option factories over openapi-fetch; server-owned basket; URL-driven server-side tables; feature folders; Caddy + Compose; specs copied and synced by script; one HttpOnly session cookie.
- [Prototype: compact shadcn design language for storefront and dashboard](tickets/11-ui-prototype.md): storefront uses the balanced boutique direction (15px, plum accent, 10px radius, ~48px rows); admin uses the compact utilitarian direction (13px, monochrome, 6px radius, 32px rows, bulk actions); prototype kept at `prototype/index.html`.
- [Grill: legal pages, checkout consent flow and record keeping](tickets/13-legal-pages-consent-flow.md): lawyer text is a launch blocker not a build blocker; static versioned Markdown legal pages; one-step checkout with obligation-stating button; per-Order Consent record plus pre-information snapshot; seller identity in Owner-edited Store settings; hygiene flag on Product class; no analytics or cookie banner; marketing consent stored but nothing sent; KVKK requests by email, deletion anonymises; non-code launch checklist.
- [Grill: product media storage, processing and delivery](tickets/15-media-storage.md): VPS disk behind one `media_url()`; async WebP derivatives, originals kept; images belong to a Product with optional colour tag and order; immutable hashed URLs via Caddy; no publish without an image; Staff can upload and delete (not the last image of a published Product); phone-friendly upload screen; no bulk import.
- [Grill: transactional email/SMS and staff notifications](tickets/16-transactional-messaging.md): email only (SMS in fog); no 'delivered' mail; outbox with retries and visible failures; SMTP relay with vendor left to the launch checklist; Turkish repo templates fed by Store settings; per-order sent-message log kept; staff get per-order email and a daily low-stock digest; guest link expires in 30 days.
- [Research: e-Arsiv invoicing options and technical requirements](tickets/14-research-earsiv-invoicing.md): three issuing routes (free GIB portal, licensed integrator with REST API and per-document pricing, direct signed UBL-TR integration with daily report); internet-sale invoices need web address, payment method and date, carrier and dispatch date; cancellation within 8 days; 5-year retention inside Turkey; portal login, buyer ID placeholder, COD receipt and pricing unconfirmed for the accountant; no GIB CSV upload exists.
- [Grill: deployment, CI/CD, backups and observability](tickets/17-deployment-ops.md): `store-infra` repo with Compose and runbook; seven services including Celery worker, beat and Redis; GitHub Actions build to GHCR, manual one-command deploy; defined CI bar plus a Playwright guest-checkout smoke test; nightly encrypted off-site backups with monthly restore drill; env-file secrets; Caddy auto-TLS; hosted error tracking and external uptime checks; brief deploy downtime accepted.
- [Grill: invoicing workflow, order data for e-Arsiv and accountant export](tickets/18-invoicing-wiring.md): manual invoicing in the free GIB portal at ship time, store holds only number/date/status; per-line KDV snapshot with shipping and COD as lines; individuals only; Owner-only audit-logged CSV export; flags for invoices needing cancellation after cancel/return; accountant items on the launch checklist; integrator API is a later option.
- [Grill: catalog browsing, search, filtering and SEO](tickets/19-catalog-search-seo.md): Postgres full-text search with Turkish normalisation and trigram typo tolerance behind one swappable module; filters on colour, size, brand, price and stock matched at Variant level; sold-out products stay visible; one list endpoint with facets and 24 per page; Turkish transliterated slugs with 301s; filtered and search pages noindex; JSON-LD, sitemap and robots; template-only SEO text; separate simple admin search.
- [Task: assemble the phase 1 PRD and architecture spec](tickets/20-assemble-spec.md): destination artifact written as `spec/prd.md` and `spec/architecture.md`, linking each section to its source ticket; cross-ticket amendments reconciled; six unverified assumptions carried as build-time checks; launch checklist gathered; ten suggested build slices.

## Not yet specified

Nothing: the way to the destination is clear.

## Out of scope

- Marketplace mode (third-party sellers, commissions, payouts): suppliers/brands are internal attributes.
- Online card payments / payment gateway (iyzico etc.): later phase.
- Native mobile app.
- Using django-oscar's own dashboard or templates.
- Later-phase work, ruled out so this map can end with the spec (each returns only as its own effort): carrier integration and per-city or weight-based shipping; self-service cancellation and returns, partial returns, automated refunds; syncing stock with the physical shop's POS; sending marketing (campaign tooling, IYS registration), analytics and a cookie banner, and a data purge job; bulk catalog import of the existing stock (launch-adjacent: load products by hand or with a one-off script outside the spec); SMS messaging; wishlist, reorder, and CAPTCHA.
