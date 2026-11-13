# Phase 1 build tickets

Sliced from the ten build slices in [architecture.md](../spec/architecture.md) section 7. Local-markdown tracker, same conventions as `../wayfinder/README.md`: a ticket is takeable when `status: open`, `assignee` is empty and every `blocked-by` ticket is closed. `type`: AFK = an agent can do it alone, HITL = needs the Owner or an operator. Tickets list scope and acceptance checks; the spec holds the reasoning. "Build check N" refers to the numbered list in architecture.md section 6.

## Slice 1: Scaffold

- 01 [store-infra: repo, Compose stack skeleton and runbook stub](tickets/01-infra.md) (store-infra, AFK)
- 02 [Backend: cookiecutter-django scaffold, CI and health endpoint](tickets/02-be-scaffold.md) (store-backend, AFK)
- 03 [Frontend: TanStack Start scaffold, tooling and CI](tickets/03-fe-scaffold.md) (store-frontend, AFK)
- 04 [Playwright smoke harness against the Compose stack](tickets/04-e2e.md) (store-infra, AFK), blocked by 01, 02, 03

## Slice 2: Backend foundation

- 05 [Backend: Oscar as an engine, without its dashboard](tickets/05-be-oscar.md) (store-backend, AFK), blocked by 02
- 06 [Backend: store and admin Ninja APIs, envelope, pagination, spec export](tickets/06-be-api.md) (store-backend, AFK), blocked by 05
- 07 [Backend: allauth app mode, token bridge, CSRF endpoint, guest auth class](tickets/07-be-auth.md) (store-backend, AFK), blocked by 06
- 08 [Backend: Owner and Staff groups, permission checks, /admin/me, audit log](tickets/08-be-roles.md) (store-backend, AFK), blocked by 06, 07
- 09 [Frontend: API proxy route, session cookie and generated clients](tickets/09-fe-proxy.md) (store-frontend, AFK), blocked by 03, 06, 07

## Slice 3: Catalog, pricing, media

- 10 [Backend: catalog models and services](tickets/10-be-catalog.md) (store-backend, AFK), blocked by 05
- 11 [Backend: KDV-inclusive pricing strategy and line snapshots](tickets/11-be-pricing.md) (store-backend, AFK), blocked by 10
- 12 [Backend: admin catalog API and stock adjustments](tickets/12-be-admin-catalog.md) (store-backend, AFK), blocked by 10, 08
- 13 [Frontend: Dashboard shell, login and reusable DataTable](tickets/13-fe-admin-shell.md) (store-frontend, AFK), blocked by 09, 08
- 14 [Frontend: Dashboard catalog and stock screens](tickets/14-fe-admin-catalog.md) (store-frontend, AFK), blocked by 13, 12
- 15 [Backend: product media upload, derivatives and delivery](tickets/15-be-media.md) (store-backend, AFK), blocked by 10, 12
- 16 [Frontend: Dashboard photo upload screen](tickets/16-fe-admin-media.md) (store-frontend, AFK), blocked by 14, 15

## Slice 4: Storefront browsing and SEO

- 17 [Backend: product list endpoint, full-text search and facets](tickets/17-be-search.md) (store-backend, AFK), blocked by 10, 06
- 18 [Frontend: storefront catalogue pages](tickets/18-fe-browse.md) (store-frontend, AFK), blocked by 09, 17, 15
- 19 [SEO: metadata, structured data, sitemap, robots, canonicals](tickets/19-seo.md) (store-frontend, AFK), blocked by 18

## Slice 5: Basket and checkout

- 20 [Backend: Store settings and Legal page versions](tickets/20-be-settings.md) (store-backend, AFK), blocked by 08
- 21 [Backend: basket API](tickets/21-be-basket.md) (store-backend, AFK), blocked by 07, 10, 11
- 22 [Backend: checkout and order placement service](tickets/22-be-checkout.md) (store-backend, AFK), blocked by 21, 20
- 23 [Frontend: basket, checkout and confirmation pages, legal pages](tickets/23-fe-checkout.md) (store-frontend, AFK), blocked by 18, 22
- 24 [Order tracking: signed link, endpoint and page](tickets/24-tracking.md) (store-backend, AFK), blocked by 22, 23

## Slice 6: Accounts

- 25 [Accounts: sign-up, verification, login, reset and account area](tickets/25-accounts.md) (store-frontend, AFK), blocked by 09, 07, 22

## Slice 7: Dashboard operations

- 26 [Backend: admin order operations](tickets/26-be-orders.md) (store-backend, AFK), blocked by 22, 08
- 27 [Frontend: Dashboard orders screens](tickets/27-fe-admin-orders.md) (store-frontend, AFK), blocked by 13, 26
- 28 [Frontend: Settings, Staff users, Customers and audit log screens](tickets/28-fe-admin-settings.md) (store-frontend, AFK), blocked by 13, 20, 08

## Slice 8: Messaging

- 29 [Backend: message outbox, worker, SMTP and bounce handling](tickets/29-be-outbox.md) (store-backend, AFK), blocked by 02, 05
- 30 [Backend: wire Shopper emails, staff notifications and timed jobs](tickets/30-be-notify.md) (store-backend, AFK), blocked by 29, 26, 20

## Slice 9: Invoicing

- 31 [Invoice record, invoice sheet and Home flags](tickets/31-invoicing.md) (store-backend, AFK), blocked by 26, 27
- 32 [Owner-only accountant CSV export](tickets/32-export.md) (store-backend, AFK), blocked by 31

## Slice 10: Hardening and launch

- 33 [Deploy script, image publishing and server baseline](tickets/33-ops-deploy.md) (store-infra, HITL), blocked by 01, 02, 03
- 34 [Backups and restore drill](tickets/34-ops-backup.md) (store-infra, AFK), blocked by 01
- 35 [Observability: error tracking, uptime and alerts](tickets/35-ops-observe.md) (store-infra, HITL), blocked by 33, 29
- 36 [Playwright: guest COD checkout end to end](tickets/36-e2e-checkout.md) (store-infra, AFK), blocked by 04, 23, 30
- 37 [Launch readiness: non-code checklist](tickets/37-launch.md) (store-infra, HITL), blocked by 33, 34, 35, 36, 32


## Phase 2

Scoped in [phase-2.md](../spec/phase-2.md) on 2026-11-12. Same conventions as above; numbering continues from 38.

### Slice 11: Catalog tidy and bulk import

- 38 [Backend: catalog API view-models and owner-only policy into services](tickets/38-be-catalog-tidy.md) (store-backend, AFK)
- 39 [Backend: bulk catalogue import service and endpoints](tickets/39-be-import.md) (store-backend, AFK), blocked by 38
- 40 [Frontend: Dashboard import screen](tickets/40-fe-import.md) (store-frontend, AFK), blocked by 39

### Slice 12: Admin shell, tabs and missing screens

- 41 [Prototype: admin shell layout (throwaway)](tickets/41-fe-shell-prototype.md) (store-frontend, HITL)
- 42 [Frontend: new admin shell with grouped sidebar and breadcrumbs](tickets/42-fe-shell.md) (store-frontend, AFK), blocked by 41
- 43 [Frontend: Settings as tabs](tickets/43-fe-settings-tabs.md) (store-frontend, AFK), blocked by 42
- 44 [Frontend: product editor as tabs](tickets/44-fe-product-tabs.md) (store-frontend, AFK), blocked by 42
- 45 [Backend: admin customers list/detail and audit-log list endpoints](tickets/45-be-customers-audit.md) (store-backend, AFK)
- 46 [Frontend: Dashboard Customers screen](tickets/46-fe-customers.md) (store-frontend, AFK), blocked by 42, 45
- 47 [Frontend: Dashboard Audit log screen](tickets/47-fe-audit-log.md) (store-frontend, AFK), blocked by 42, 45

### Slice 13: Carrier rates

- 48 [Backend: split checkout into quote and place](tickets/48-be-checkout-split.md) (store-backend, AFK)
- 49 [Backend: carriers, zones, desi and rate tables](tickets/49-be-shipping.md) (store-backend, AFK), blocked by 48
- 50 [Backend: checkout and orders use the computed shipping fee](tickets/50-be-checkout-fees.md) (store-backend, AFK), blocked by 49
- 51 [Frontend: Dashboard carrier rates screen and desi fields](tickets/51-fe-admin-rates.md) (store-frontend, AFK), blocked by 49, 44
- 52 [Frontend: checkout shows the computed shipping fee](tickets/52-fe-checkout-fee.md) (store-frontend, AFK), blocked by 50
- 53 [Load real carrier prices and desi values](tickets/53-ops-real-rates.md) (store-infra, HITL), blocked by 51

### Slice 14: Self-service returns

- 54 [Backend: outbox transport and scheduler seams](tickets/54-be-outbox-seam.md) (store-backend, AFK)
- 55 [Backend: Shopper cancel and return requests](tickets/55-be-return-requests.md) (store-backend, AFK), blocked by 54
- 56 [Backend: Staff approve or reject returns, emails and restocking](tickets/56-be-return-review.md) (store-backend, AFK), blocked by 55
- 57 [Frontend: Shopper cancel and return request pages](tickets/57-fe-returns.md) (store-frontend, AFK), blocked by 55
- 58 [Frontend: Dashboard return requests](tickets/58-fe-admin-returns.md) (store-frontend, AFK), blocked by 56, 42
- 59 [Return wording and return-carrier terms approved](tickets/59-ops-returns-legal.md) (store-infra, HITL), blocked by 55

### Slice 15: Legal pages in the Dashboard

- 60 [Backend: Legal pages as immutable versions, Owner edit API, search](tickets/60-be-legal-pages.md) (store-backend, AFK)
- 61 [Frontend: Storefront Legal pages from the API, with search](tickets/61-fe-storefront-legal.md) (store-frontend, AFK), blocked by 60
- 62 [Frontend: Dashboard Legal pages editor (Owner)](tickets/62-fe-admin-legal.md) (store-frontend, AFK), blocked by 60
