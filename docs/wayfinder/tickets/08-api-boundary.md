---
title: Grill: API boundary between Ninja and Oscar (storefront vs admin APIs)
label: wayfinder:grilling
status: closed
assignee: claude-session
blocked-by: [01-research-oscar-fit.md, 02-research-headless-auth.md, 03-research-tanstack-start.md, 12-spike-headless-oscar-and-ssr-cookies.md]
parent: map.md
---

## Question

Decide: how django-ninja wraps Oscar (service layer vs direct ORM), split of storefront API vs admin API, auth/permission enforcement, pagination/filtering conventions for TanStack Table, error format, OpenAPI as the contract and how the frontend consumes it, versioning.

## Resolution

Decided with the user (recommended answers accepted; Q2 amended to use allauth app mode, then Q2b/Q3b accepted).

1. **Service layer.** Ninja handlers stay thin (parse, call a service, map to schema). Services own business rules (place order, reserve stock, mark paid, price/KDV, SKU) and wrap Oscar's `OrderCreator`, strategies and models, one transaction per use case. Schemas are hand-written, never generated from Oscar models.
2. **Two APIs.** Two `NinjaAPI` instances: `/api/store/` (public: catalog, basket, checkout, tracking, account) and `/api/admin/` (staff only), each with its own OpenAPI spec, auth rules and generated client; both use the shared service layer. allauth headless is mounted in **app mode** (`/_allauth/app/v1/`), not browser mode, which overrides the spike's recommendation.
3. **Token bridge (Q2b).** A small middleware before Oscar's `BasketMiddleware` reads `X-Session-Token`, loads that Django session and sets `request.user`, so basket merge and customer baskets work unchanged. NOT yet verified (the spike only showed `request.auth` is set); the first build ticket must prove it as an acceptance check, and falls back to allauth browser mode if it fails.
4. **CSRF (Q3b).** Token-authenticated customer and admin endpoints need no CSRF check (the token is never sent automatically). Guest endpoints (basket, checkout) rely on Oscar's signed basket cookie, so they use a custom auth class with an explicit CSRF check; the token comes from `GET /api/store/csrf`. Only the Start server holds the session token (its own HttpOnly cookie); browser JS never sees it. The whole admin API requires an authenticated staff user at API level; per-action permissions belong to the roles ticket.
5. **Lists.** Offset pagination `page`/`page_size` (max 100) returning `{items, total, page, page_size}`; `ordering=-field` with a per-endpoint allowlist; explicit filter params per endpoint via `FilterSchema`, plus `q` text search.
6. **Errors.** One envelope `{code, message, fields}` produced by a global exception handler: stable machine `code`, Turkish `message`, `fields` as field to messages for TanStack Form. Statuses: 400/422 validation, 401, 403, 404, 409 state conflicts (e.g. stock gone mid-checkout), 429 rate limit.
7. **Contract.** Backend commits exported specs (`openapi-store.json`, `openapi-admin.json`) with explicit short operationIds (`catalog_list_products`). Frontend generates types with `openapi-typescript` and calls via `openapi-fetch`, wrapped in our own TanStack Query option helpers (hey-api rejected as 0.x). CI fails if a committed spec is stale.
8. **Versioning.** None in phase 1 (lockstep deploys); add `/v2` only if a second client appears.
9. **Wire formats.** Money is a decimal string in TRY, KDV-inclusive (`"349.90"`), derived KDV returned separately where needed. Storefront identifies Products by slug and Orders by Order number; other ids are opaque numerics; admin uses numeric ids; no sequential internal Order ids publicly.
