---
title: Grill: frontend architecture and app structure
label: wayfinder:grilling
status: closed
assignee: claude (serkanbulutgenc@gmail.com session)
blocked-by: [03-research-tanstack-start.md, 08-api-boundary.md]
parent: map.md
---

## Question

Decide: route layout (storefront vs /admin), data layer conventions with Query, form/table conventions, state for basket/checkout, SSR vs client rendering per route, repo structure, folder conventions, deployment shape (Node server behind reverse proxy).

## Resolution

Decided with the user (all recommended answers accepted; Q3 amended so admin paths stay English).

1. **Browser-to-Django path.** Django is private; the browser only talks to the Start server. One catch-all proxy route (`/api/**`) attaches the session token and guest basket cookie and forwards to Django. SSR loaders use the same helper, so the generated `openapi-fetch` clients work on server and browser. Fallback if Start's cookie API fails: browser calls Django directly on a sub-path through the reverse proxy.
2. **Rendering.** Storefront catalogue and product pages: SSR with Query streaming. Basket, checkout, account, order tracking: SSR shell with client-side data. Whole `/admin` tree: `ssr: false`.
3. **Routes.** One app, two layout branches. Storefront at `/` with Turkish slugs (`/urun/$slug`, `/kategori/$slug`, `/sepet`, `/odeme`, `/siparis-takip`) and a pathless `_customer` guard for account pages. Admin under `/admin` with English paths (`/admin/products`, `/admin/orders`), guarded in `beforeLoad` by `GET /api/admin/me`, login at `/admin/login`. Guards are UX only.
4. **Data layer.** `src/lib/api` holds generated types and two `openapi-fetch` clients (store, admin). Hand-written `queryOptions` factories per resource, loaders prefetch with them, mutations invalidate by key. The `{code, message, fields}` envelope maps once to a typed `ApiError`; `fields` feeds TanStack Form.
5. **Basket and checkout.** The Oscar basket is the only source of truth; no client copy beyond the Query cache, no localStorage. Checkout is one page with sections in a single TanStack Form with a Zod schema. A 409 (stock gone) shows inline and refreshes the basket.
6. **Admin tables and forms.** One reusable `DataTable` (shadcn + Table v9, server-side mode). Page, size, ordering, filters and `q` live in validated URL search params. Edit forms use TanStack Form + Zod, with a dirty-leave guard on long forms.
7. **Structure and deployment.** Feature folders (`src/features/{catalog,basket,checkout,account,admin/*}` with `queries.ts`, `components/`, `schemas.ts`), `src/components/ui`, `src/lib/api`, thin `src/routes`. Docker Compose on the VPS: Caddy (TLS, one public origin) to the Start Node server; Django, Postgres private; media served by the proxy from a shared volume.
8. **Contract sync.** The backend commits the specs; the frontend commits copies under `api/specs/` plus generated `.d.ts`. `pnpm api:sync` copies from the sibling backend checkout and regenerates. Frontend CI only checks types match the committed specs; no cross-repo fetch.
9. **Session.** One HttpOnly, Secure, SameSite=Lax cookie holds the allauth token for customers and staff. Root `beforeLoad` loads `/me` into router context; the admin guard also loads the permission list and exposes `can('<permission>')`. A 401 clears the cookie and redirects to login; a 403 shows a toast.
10. **Feedback conventions.** Skeleton `pendingComponent` per route; shared `errorComponent` (retry plus envelope message) and `notFoundComponent`; sonner toasts for mutations, inline field errors. Money formatted from decimal strings with `Intl.NumberFormat('tr-TR')`, never client-side arithmetic; dates `tr-TR`, `Europe/Istanbul`.
11. **Tooling.** pnpm, strict TypeScript, Biome, `@/` alias, validated `env.ts` (`API_INTERNAL_URL`, `SESSION_COOKIE_NAME`), Node 22 LTS image.

**Acceptance checks carried to build tickets (unverified):** (a) Start's `getRequest` and response `Set-Cookie` handling inside the proxy route; (b) Table v9 manual (server-side) mode; (c) `queryClient.query` vs `ensureQueryData` in the installed react-query.
