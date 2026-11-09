---
title: Research: TanStack Start maturity and admin-dashboard building blocks
label: wayfinder:research
status: closed
assignee:
blocked-by: []
parent: map.md
---

## Question

Establish facts: TanStack Start current release/stability and production deployment (Node, Docker); SSR data loading with Query; typed OpenAPI client generation options for consuming django-ninja schemas; TanStack Table/Form with shadcn/ui for data-heavy admin screens; auth-guarded route patterns. Write findings to research/03-tanstack-start.md with source links.

## Resolution

Findings: `../research/03-tanstack-start.md`.

- TanStack Start is documented as stable (react-start 1.169.5, 2026-11-08); RSC experimental. Node deploy = Vite build + `nitro` plugin, `node .output/server/index.mjs`; Nitro is on a 3.x RC and docs call its Vite plugin under active development.
- Query SSR via `@tanstack/react-router-ssr-query`; `useSuspenseQuery` streams, `useQuery` is client-only. Docs now show `queryClient.query` and mark `ensureQueryData` deprecated (verify against installed version).
- django-ninja 1.8.0 emits OpenAPI 3.1.0 at `/openapi.json` (plus `export_openapi_schema`); default operationIds are long. Candidates: hey-api/openapi-ts (0.x, generates TanStack Query options) or openapi-typescript + openapi-fetch.
- Table v9 + shadcn data-table and TanStack Form 1.x (Standard Schema/Zod, shadcn guide) exist; auth guards via pathless `_authenticated` + `beforeLoad` are UX only, data endpoints must authorise. Unconfirmed: Table v9 server-side mode, allauth cookie forwarding from Start.
