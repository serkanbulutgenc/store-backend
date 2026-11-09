# Aura Tekstil online store: phase 1 architecture

Companion to [prd.md](prd.md). Assembled from the closed wayfinding tickets (`../wayfinder/map.md`); each section links the ticket holding the detail. Build tickets should be sliced from this document and the PRD.

## 1. Repositories and runtime

Three repos, one deployed stack ([deployment](../wayfinder/tickets/17-deployment-ops.md), [frontend architecture](../wayfinder/tickets/10-frontend-architecture.md)):

- `store-backend` (`/home/sbulutgenc/Projects/Python/store-backend`): cookiecutter-django with django-oscar (engine only), django-ninja, django-allauth. Python 3.14, Django 6.0, Oscar 4.2.1, Postgres 17 (SQLite is unusable: cookiecutter's `sites` migration is Postgres-only).
- `store-frontend` (`/home/sbulutgenc/Projects/Web/store-frontend`): one TanStack Start app, storefront at `/`, dashboard under `/admin`. pnpm, strict TypeScript, Biome, Node 22 LTS image.
- `store-infra` (new): Compose file, Caddyfile, env templates, deploy script, runbook.

Production services on one VPS (2 vCPU, 4 GB, 40 GB SSD minimum, EU region): `caddy` (the only public service, TLS via Let's Encrypt, serves media from a shared volume, maintenance page), `web` (Start Node server), `api` (Django, gunicorn), `worker` (Celery), `beat` (scheduler), `postgres` (named volume), `redis` (broker only). Django, Postgres and Redis sit on a private Compose network.

## 2. Backend

**Oscar as an engine** ([Oscar fit](../wayfinder/research/01-oscar-fit.md), [spike](../wayfinder/tickets/12-spike-headless-oscar-and-ssr-cookies.md)):
- Oscar's own URLs, templates and dashboard are not used. Every `oscar.apps.dashboard.*` app is dropped and a subclass of `oscar.apps.dashboard.apps.DashboardConfig` with a no-op `ready()` is registered (Oscar's `Shop.ready()` and the `dashboard_tags` module need the `dashboard` app label). `oscar.config.Shop` stays.
- Settings needed: `from oscar.defaults import *`, the Oscar app list, `django.contrib.flatpages`, `widget_tweaks`, `haystack` with the simple backend, `treebeard`, `django_tables2`, and `BasketMiddleware`. `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` must include the frontend origin.
- Stored Oscar price is tax-exclusive; a custom pricing strategy returns the KDV-inclusive shelf price with the rate from the Product class. Partner is a fulfilment concept, so Brand and Supplier are custom models. Offline payments (bank transfer, COD) are custom Source and Transaction records.

**Service layer** ([API boundary](../wayfinder/tickets/08-api-boundary.md)):
- Ninja handlers parse, call a service and map to a schema. Services own business rules (place order, reserve stock, mark paid, price and KDV, SKU generation, invoice flags) and wrap Oscar's `OrderCreator`, strategies and models, one transaction per use case. Schemas are hand-written, never generated from Oscar models.

**Two APIs.** Two `NinjaAPI` instances: `/api/store/` (public: catalog, basket, checkout, tracking, account) and `/api/admin/` (authenticated staff only; each endpoint declares one named permission, missing permission returns 403). Each has its own committed OpenAPI spec (`openapi-store.json`, `openapi-admin.json`) with explicit short operationIds such as `catalog_list_products`. CI fails if a committed spec is stale. No versioning in phase 1.

**Conventions.** Offset pagination `{items, total, page, page_size}` (`page_size` max 100); `ordering=-field` from a per-endpoint allowlist; explicit filter params plus `q`. One error envelope `{code, message, fields}` (Turkish `message`; statuses 400/422, 401, 403, 404, 409 state conflicts such as stock gone mid-checkout, 429). Money is a decimal string in TRY, KDV-inclusive (`"349.90"`), derived KDV returned separately where needed. Storefront identifies Products by slug and Orders by Order number; no sequential internal Order ids are public.

**Auth and sessions** ([API boundary](../wayfinder/tickets/08-api-boundary.md), [roles](../wayfinder/tickets/09-admin-roles-permissions.md)):
- allauth headless in **app mode** (`/_allauth/app/v1/`); the Start server holds the session token. A small middleware before `BasketMiddleware` reads `X-Session-Token`, loads that Django session and sets `request.user`, so Oscar's basket merge works. Token-authenticated endpoints need no CSRF check. Guest endpoints (basket, checkout) rely on Oscar's signed `oscar_open_basket` cookie, use a custom auth class with an explicit CSRF check, and get the token from `GET /api/store/csrf`. The CSRF cookie is HttpOnly, so JS cannot read it.
- Oscar guest-checkout settings are enabled (off by default); basket cookie lifetime 14 days, `Secure` in production.
- Two fixed groups, Owner and Staff, seeded by migration. `GET /api/admin/me` returns the permission list. Owner-only fields (cost price) are omitted from responses for Staff. Owner has TOTP 2FA (allauth MFA); Staff optional; Staff invited by email, deactivated never deleted.

**Domain additions beyond Oscar** (see `/CONTEXT.md`): Brand, Supplier, Colour list, Size scheme per Product class, Variant colour and size, Product image (Product, optional Colour, sort order, primary, alt text), Store settings singleton, Legal page version identifiers, Consent record, Pre-information snapshot, per-line KDV snapshot, invoice record (number, date, status, credit-note number and date), buyer tax number and company title on the Order, outbox Message log, Audit log, low-stock threshold, hygiene flag on Product class, slug redirect table.

**Order state** ([order lifecycle](../wayfinder/tickets/06-order-lifecycle-payments.md)): fulfilment status via Oscar's order pipeline; payment status derived from payment Source and Transaction records. Stock is reserved at placement and consumed on shipping; Celery beat sends the 24 h reminder and runs the 48 h auto-cancel that releases stock.

**Search** ([catalog search](../wayfinder/tickets/19-catalog-search-seo.md)): Postgres full-text search with a Turkish configuration and `unaccent`, plus a trigram index for typo tolerance and Turkish dotted/dotless-i normalisation; behind one search-service module (the swap point for Meilisearch later). `GET /api/store/products` serves category pages and search with facets and 24 per page. Admin search is a separate simple database match with exact SKU, barcode and order number lookup.

**Media** ([media](../wayfinder/tickets/15-media-storage.md)): originals and derivatives on the VPS volume behind one `media_url()` function. On upload a Celery job keeps the original and generates WebP derivatives (about 400, 800 and 1600 px), strips EXIF and applies orientation. Content-hashed, immutable URLs, `Cache-Control: public, max-age=31536000, immutable`. Deleted files are removed after a grace period.

**Messaging** ([messaging](../wayfinder/tickets/16-transactional-messaging.md)): outbox pattern. The event writes a Message record in the same transaction as the Order change; a Celery worker sends it through an SMTP relay with about 5 retries over 24 h. Provider bounce and complaint webhooks mark messages bounced. Templates are Turkish HTML plus plain text in the repo, with shop details from Store settings. Sent messages (recipient, template, time, status, rendered body) are kept and shown on Order detail. Staff get an email per new order and a daily low-stock digest.

**SEO endpoints:** Django serves `sitemap.xml` and `robots.txt` (through the Start proxy on the single public origin).

## 3. Frontend

([frontend architecture](../wayfinder/tickets/10-frontend-architecture.md), [prototype](../wayfinder/tickets/11-ui-prototype.md))

- **Proxy:** Django is private; the browser talks only to the Start server. One catch-all `/api/**` route attaches the session token and the guest basket cookie and forwards to Django; SSR loaders use the same helper. Fallback if Start's cookie handling fails: the browser calls Django directly on a sub-path through Caddy.
- **Rendering:** catalogue and product pages SSR with Query streaming; basket, checkout, account and order tracking SSR shell with client data; the whole `/admin` tree `ssr: false`.
- **Routes:** storefront Turkish slugs (`/urun/$slug`, `/kategori/$slug`, `/ara`, `/sepet`, `/odeme`, `/siparis-takip`) with a pathless `_customer` guard for account pages; admin English paths (`/admin/products`, `/admin/orders`) guarded in `beforeLoad` by `/me`, login at `/admin/login`. Guards are UX only; the API enforces access.
- **Data layer:** `openapi-typescript` types and two `openapi-fetch` clients (store, admin); hand-written `queryOptions` factories per resource; loaders prefetch; mutations invalidate by key. The error envelope maps once to a typed `ApiError`; `fields` feeds TanStack Form.
- **Basket and checkout:** the Oscar basket is the only source of truth, no localStorage. Checkout is one page, one TanStack Form with a Zod schema; a 409 shows inline and refreshes the basket.
- **Admin tables and forms:** one reusable `DataTable` (shadcn on Table v9, server-side mode) with page, size, ordering, filters and `q` in validated URL search params; forms use TanStack Form and Zod with a dirty-leave guard.
- **Structure:** feature folders (`src/features/{catalog,basket,checkout,account,admin/*}` with `queries.ts`, `components/`, `schemas.ts`), `src/components/ui`, `src/lib/api`, thin `src/routes`.
- **Contract sync:** specs copied under `api/specs/` with generated `.d.ts`; `pnpm api:sync` copies from the sibling backend checkout and regenerates. Frontend CI checks types match the committed specs.
- **Session:** one HttpOnly, Secure, SameSite=Lax cookie holding the allauth token for customers and staff. A 401 clears it and redirects; a 403 shows a toast.
- **Conventions:** skeleton `pendingComponent`, shared `errorComponent` and `notFoundComponent`, sonner toasts; money formatted from decimal strings with `Intl.NumberFormat('tr-TR')`, no client arithmetic; dates `tr-TR`, `Europe/Istanbul`.
- **Design language:** storefront uses the balanced boutique direction (15px, plum accent #6b2d5c, 10px radius, about 48px rows); Dashboard uses the compact utilitarian direction (13px, monochrome, 6px radius, 32px rows, bulk actions). Reference: `../wayfinder/prototype/index.html`.
- **Legal pages:** static Markdown in the frontend repo, one version identifier each, recorded with every Order. Storefront emits `Product` and `BreadcrumbList` JSON-LD and server-rendered metadata ([catalog search](../wayfinder/tickets/19-catalog-search-seo.md)).

## 4. Delivery, operations and quality

([deployment](../wayfinder/tickets/17-deployment-ops.md))

- **CI:** GitHub Actions per repo. Backend: pytest on real Postgres, ruff, `makemigrations --check`, OpenAPI spec drift check. Frontend: Biome, strict `tsc`, Vitest, spec-sync check. One Playwright smoke test of guest COD checkout against the Compose stack. Images are versioned and pushed to GHCR on merge to `main`.
- **Deploy:** a manual one-command script in `store-infra` (pull tags, migrate, restart); rollback re-runs it with the previous tag; migrations must be backwards-compatible with the previous release for one deploy; deploys happen outside trading hours; brief downtime accepted.
- **Backups:** nightly compressed `pg_dump`, nightly incremental media sync, weekly provider snapshot, encrypted client-side to an off-site provider; retention 14 daily, 8 weekly, 6 monthly; scripted monthly restore drill; alert if no successful backup in 26 hours.
- **Secrets:** env files on the VPS (mode 600) from templates; encryption keys held off-VPS; CI secrets in GitHub Actions secrets; manual rotation in the runbook.
- **Observability:** JSON logs to stdout with Docker rotation; hosted error tracking with PII scrubbing; external uptime check on the storefront and an API health endpoint that checks Postgres and Redis; alerts for backup age, outbox messages failing after all retries, and disk over 80%. No metrics stack.
- **Hardening:** SSH keys only, no root login, firewall on 80/443/SSH, unattended security updates, fail2ban, Postgres and Redis not published.

## 5. Privacy, retention and invoicing

- Nothing is hard-deleted in phase 1. Account deletion anonymises the profile and optional extras; Orders keep their snapshot name and billing address for the legal period because that is invoice data. KVKK requests are handled by email within 30 days; the Owner acts on them.
- Consent: marketing consent is stored on the Shopper with its own timestamp and can be toggled by a Customer; nothing is sent in phase 1.
- Invoices are issued by hand in the GIB portal, so the system never stores the invoice document (5-year retention inside Turkey stays with the portal and accountant). The Owner-only CSV export is audit-logged. An integrator API is a later option; the invoice record is shaped so it can be filled by one ([invoicing](../wayfinder/tickets/18-invoicing-wiring.md)).

## 6. Build-time checks (unverified assumptions)

Each is an acceptance check on the first ticket that touches it; the fallback is noted.

1. The token-to-`request.user` middleware lets Oscar's basket merge and customer baskets work unchanged. Fallback: allauth browser mode ([API boundary](../wayfinder/tickets/08-api-boundary.md)).
2. Start's `getRequest` and response `Set-Cookie` handling inside the proxy route keeps one guest basket and one login across SSR and browser (the spike proved only the HTTP mechanics). Fallback: browser calls Django directly on a sub-path ([frontend architecture](../wayfinder/tickets/10-frontend-architecture.md)).
3. Table v9 server-side (manual) mode; `queryClient.query` versus `ensureQueryData` in the installed react-query.
4. HEIC phone photos convert on upload with the chosen image library; otherwise reject with a Turkish message ([media](../wayfinder/tickets/15-media-storage.md)).
5. The custom pricing strategy returns KDV-inclusive prices with correct per-line rounding that matches Order totals.
6. Postgres Turkish full-text configuration and `unaccent` behave as expected for dotted and dotless i.

## 7. Suggested build slices

Slices that can be ticketed independently, in dependency order:
1. Scaffold the three repos, CI, `store-infra` Compose, health endpoint, and the Playwright smoke harness.
2. Backend foundation: Oscar-as-engine settings and the dashboard shim, custom user, allauth app mode, the token bridge, error envelope, the two Ninja APIs and spec export.
3. Catalog model, KDV pricing strategy, Colour list, Brand and Supplier, SKUs, stock; admin Product screens; media upload and processing.
4. Storefront browsing, search, filters, SEO, sitemap.
5. Basket, guest checkout, consent records, snapshots, order placement, bank-transfer and COD flows, order tracking.
6. Accounts, address book, claiming.
7. Dashboard orders, stock, settings, staff users, roles, audit log.
8. Messaging outbox, templates, staff notifications.
9. Invoice sheet, invoice record and export, flags on Home.
10. Hardening, backups, monitoring, restore drill, launch checklist.
