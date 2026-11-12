---
title: Frontend: TanStack Start scaffold, tooling and CI
repo: store-frontend
slice: 1
type: AFK
status: closed
assignee:
blocked-by: []
---

## Scope

- TanStack Start app with Router and Query, shadcn/ui, Biome, strict TypeScript, Vitest, pnpm, `@/` alias, validated `env.ts` (`API_INTERNAL_URL`, `SESSION_COOKIE_NAME`).
- Theme tokens for both directions: storefront (15px, plum #6b2d5c, 10px radius) and admin (13px, monochrome, 6px radius), per `docs/wayfinder/prototype/index.html` in the backend repo.
- Node 22 Dockerfile (Vite build + Nitro, `node .output/server/index.mjs`); GitHub Actions: Biome, `tsc`, Vitest.
- Feature-folder skeleton and thin `src/routes`.

## Acceptance

- [ ] CI is green; production image builds and serves a placeholder storefront page and `/admin` page. (pending: first push to GitHub)
- [x] Both themes are selectable per layout branch.

## Spec

- [Architecture section 3](../../spec/architecture.md)

## Resolution

TanStack Start, Router/Query, Tailwind 4 with shadcn-style primitives, Biome, strict TypeScript 7, Vitest, pnpm 10 (pinned via `packageManager`), validated `env.ts`. Themes are `[data-theme="storefront"]` and `[data-theme="admin"]` set by the `_storefront` and `admin` layout routes; `/admin` is `ssr: false`.

- Verified by the Owner in the `store-infra` dev stack: the production image builds, `web` is `(healthy)`, `/` renders `data-theme="storefront"` and `/admin` serves its shell through Caddy. Lint, `tsc`, Vitest and `pnpm build` pass locally.
- Open: CI has not run on GitHub yet, and the `/admin` theme and styling have not been looked at in a browser.
- Biome is scoped to explicit files in `biome.json`; the workspace had placeholder dotfiles it could not read.
