# Research 03: TanStack Start maturity and admin-dashboard building blocks

Researched 2026-11-09. Facts only, no design decisions. Each claim cites its source. Doc pages were read through a summarising fetcher (not raw), so exact wording should be re-checked before quoting. Items marked NOT CONFIRMED were not found in the sources read.

## 1. Release and stability

- Latest `@tanstack/react-start` on npm: 1.169.5, published 2026-11-08 (dist-tags also carry `beta`, `alpha`, `pre`). [npm registry](https://registry.npmjs.org/@tanstack/react-start), [GitHub releases](https://github.com/TanStack/router/releases)
- Docs overview states "TanStack Start is stable" and links a "Migrate from the Release Candidate" guide, so an RC phase existed and is over. [Start overview](https://tanstack.com/start/latest/docs/framework/react/overview)
- Same page: React Server Components are experimental. Start builds with Vite or Rsbuild. [Start overview](https://tanstack.com/start/latest/docs/framework/react/overview)
- Related versions on npm (2026-11-09): react-router 1.170.46, react-query 5.107.1, react-table 9.3.1, react-form 1.33.5 (a 2.0.0-alpha exists as a dist-tag). [npm: router](https://registry.npmjs.org/@tanstack/react-router), [query](https://registry.npmjs.org/@tanstack/react-query), [table](https://registry.npmjs.org/@tanstack/react-table), [form](https://registry.npmjs.org/@tanstack/react-form)

## 2. Production deployment (Node, Docker)

- Official hosting partners: Cloudflare, Netlify, Railway. Node.js/Docker, Bun, Vercel, Appwrite Sites also documented. [Hosting guide](https://tanstack.com/start/latest/docs/framework/react/guide/hosting)
- Node (Vite build): install `nitro`, add the `nitro/vite` plugin; scripts `"build": "vite build"`, `"start": "node .output/server/index.mjs"`. [Hosting guide](https://tanstack.com/start/latest/docs/framework/react/guide/hosting)
- Rsbuild build: client assets in `dist/client`, server bundle `dist/server/index.js`; serve statics and forward other requests to the server entry's `fetch` handler, e.g. `srvx --prod -s ../client dist/server/index.js`. [Hosting guide](https://tanstack.com/start/latest/docs/framework/react/guide/hosting)
- The docs say the Nitro Vite plugin is "still under active development". npm `nitro` latest is `3.0.261103-rc` (a release candidate). [Hosting guide](https://tanstack.com/start/latest/docs/framework/react/guide/hosting), [npm: nitro](https://registry.npmjs.org/nitro)
- Optional Node tweak: about 5% throughput gain with `srvx` and `globalThis.Response = FastResponse` in the server entry. [Hosting guide](https://tanstack.com/start/latest/docs/framework/react/guide/hosting)
- No official Dockerfile was found in the docs read; the docs treat Docker as "run the Node output". NOT CONFIRMED: an official Docker example.

## 3. SSR data loading with Query

- Package `@tanstack/react-router-ssr-query` handles dehydration, hydration and streaming. Create a new `QueryClient` per request inside `getRouter()`, put it in router context, then call `setupRouterSsrQueryIntegration({ router, queryClient })`. [Router: Query integration](https://tanstack.com/router/latest/docs/integrations/query)
- Options: `wrapQueryClient: false` (use your own provider), `handleRedirects: false`, `dehydrateOptions`, `hydrateOptions`. [same](https://tanstack.com/router/latest/docs/integrations/query)
- `useSuspenseQuery` runs on the server and streams; `useQuery` does not run on the server. [same](https://tanstack.com/router/latest/docs/integrations/query)
- Loaders: return the promise for a blocking fetch; start without awaiting/returning for a non-blocking stream. [same](https://tanstack.com/router/latest/docs/integrations/query)
- Doc inconsistency to note: the fetched page shows `context.queryClient.query({...opts, staleTime: 'static'})` in loaders and marks `ensureQueryData`/`prefetchQuery` as deprecated, but the installed react-query is 5.107.1. Verify `queryClient.query` exists in the installed version before relying on it. [same](https://tanstack.com/router/latest/docs/integrations/query)

## 4. Typed OpenAPI client generation from django-ninja

django-ninja side (source: [repo](https://github.com/vitalik/django-ninja), [PyPI](https://pypi.org/project/django-ninja/)):
- Latest 1.8.0; requires Django >= 3.1 and pydantic >= 2.0, < 3.
- Emits OpenAPI `3.1.0` (`ninja/openapi/schema.py`). [source](https://github.com/vitalik/django-ninja/blob/master/ninja/openapi/schema.py)
- Schema served at `openapi_url`, default `"/openapi.json"` (so `/api/openapi.json` when mounted at `/api/`); interactive docs at `/api/docs`. [ninja/main.py](https://github.com/vitalik/django-ninja/blob/master/ninja/main.py)
- Offline export: `python manage.py export_openapi_schema [--api project.urls.api]` (`ninja/management/commands/export_openapi_schema.py`). [source](https://github.com/vitalik/django-ninja/blob/master/ninja/management/commands/export_openapi_schema.py)
- Default `operationId` = module path + function name with dots as underscores (`get_openapi_operation_id`), so generated client function names will be long unless `operation_id=` is set per operation or the method is overridden. Duplicate IDs produce a warning. [ninja/main.py](https://github.com/vitalik/django-ninja/blob/master/ninja/main.py), [schema.py](https://github.com/vitalik/django-ninja/blob/master/ninja/openapi/schema.py)

Generator options:
| Option | Facts | Source |
|---|---|---|
| `@hey-api/openapi-ts` 0.99.0 (2026-06-22, pre-1.0) | Code generator "accepts any OpenAPI specification"; 20+ plugins incl. Zod and TanStack Query; Fetch/Axios/Next/Nuxt clients; Node 22+. TanStack Query plugin generates `queryOptions` (`getPetByIdOptions`), `infiniteQueryOptions` (when a pagination param is detected), `mutationOptions` (`addPetMutation`), plus query keys (`tags: true` adds tags to keys). Plugin page has no SSR notes. | [repo README](https://github.com/hey-api/openapi-ts), [plugin docs](https://heyapi.dev/openapi-ts/plugins/tanstack-query), [npm](https://registry.npmjs.org/@hey-api/openapi-ts) |
| `openapi-typescript` 7.13.0 + `openapi-fetch` 0.17.0 | Generates `.d.ts` types from a schema file (`npx openapi-typescript ./spec -o ./src/lib/api/v1.d.ts`); `createClient<paths>({ baseUrl })`; `client.GET(path, {params})` returns `data`/`error`/`response`; ~6 kB; Node 18+. Docs do not state supported OpenAPI versions (NOT CONFIRMED for 3.1 from the page read). | [docs](https://openapi-ts.dev/openapi-fetch/), [npm](https://registry.npmjs.org/openapi-typescript) |
| `openapi-react-query` 0.5.4 | Companion with useQuery, useMutation, useSuspenseQuery, useInfiniteQuery, queryOptions pages; 0.x. Details not read. | [docs sidebar](https://openapi-ts.dev/openapi-fetch/), [npm](https://registry.npmjs.org/openapi-react-query) |

## 5. Table, Form, shadcn/ui

- shadcn data-table guide is built on TanStack Table v9 (`@tanstack/react-table` 9.3.1 is `latest` on npm). Install: `shadcn add table` + `@tanstack/react-table`. Covers pagination (10/page default), sorting, column filter, column visibility, row selection, row-action menus, reusable header/pagination/visibility components, RTL. Features declared via `tableFeatures()` so unlisted features are excluded from the bundle. The visible guide has no server-side/manual pagination section; it points to the Table pagination docs (that URL returned 404 when fetched, so manual-mode API NOT CONFIRMED here). [shadcn data-table](https://ui.shadcn.com/docs/components/data-table), [npm](https://registry.npmjs.org/@tanstack/react-table)
- TanStack Form: React adapter `@tanstack/react-form` 1.33.5; async validators with debounce (`onChangeAsync`, `onChangeAsyncDebounceMs`); natively supports Standard Schema libraries (Zod, Valibot, ArkType, Effect Schema), form-level schema; schema validation does not return transformed values. [overview](https://tanstack.com/form/latest/docs/overview), [validation](https://tanstack.com/form/latest/docs/framework/react/guides/validation)
- shadcn has an official TanStack Form guide using `Field`, `FieldLabel`, `FieldError`, `FieldGroup`, etc. with `form.Field`; Zod schema via `validators: { onSubmit: schema }`; supports array fields (`mode="array"`, `pushValue`, `removeValue`). The guide's example uses Zod v3. [shadcn TanStack Form](https://ui.shadcn.com/docs/forms/tanstack-form)
- NOT CONFIRMED: TanStack Form SSR/Start-specific integration (not covered in pages read).

## 6. Auth-guarded routes

- Pattern: a pathless layout route (e.g. `_authenticated`) whose `beforeLoad` throws `redirect({ to: '/login', search: { redirect: location.href } })`; all child routes inherit it. After login use `router.history.push(search.redirect)`. Catch check failures but rethrow with `isRedirect(error)`. Auth state can be passed through router context (`createRootRouteWithContext`). [Router: authenticated routes](https://tanstack.com/router/latest/docs/framework/react/guide/authenticated-routes)
- Both docs state guards are UX only: every server function/route/API returning private data must authorise itself. [authenticated routes](https://tanstack.com/router/latest/docs/framework/react/guide/authenticated-routes), [Start auth guide](https://tanstack.com/start/latest/docs/framework/react/guide/authentication)
- Start has no built-in session API; it provides server functions and cookie utilities. The guide's example uses iron-session (encrypted cookie sessions, secret >= 32 chars, httpOnly/sameSite/secure), loads the user in root `beforeLoad` for SSR consistency, and suggests `Cache-Control: private, no-store` for session-specific responses. Hosted alternatives named: Clerk, WorkOS, Better Auth, Auth.js. [Start auth guide](https://tanstack.com/start/latest/docs/framework/react/guide/authentication)
- Server functions: `createServerFn()`, default GET, `{ method: 'POST' }` for writes; `getRequest()` and `getRequestHeader(name)` from `@tanstack/react-start/server` are documented; middleware (e.g. `authMiddleware`, `createCsrfMiddleware`) can be attached. [Server functions](https://tanstack.com/start/latest/docs/framework/react/guide/server-functions)
- NOT CONFIRMED: documented pattern for forwarding the browser's django-allauth session cookie to a separate Django API from Start server functions/loaders (the Start auth guide does not cover it; cookie helpers beyond `getRequestHeader` were not confirmed in the pages read).

## Gaps for follow-up (if wanted)
- Re-verify `queryClient.query` vs `ensureQueryData` against installed react-query.
- Table v9 manual pagination/sorting/filtering API.
- Cookie/CSRF forwarding between Start server and Django (allauth headless).
- Official Docker example, if any; Nitro 3 RC status.
