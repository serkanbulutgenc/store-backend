---
title: Frontend: Dashboard shell, login and reusable DataTable
repo: store-frontend
slice: 3
type: AFK
status: closed
assignee:
blocked-by: [09-fe-proxy.md, 08-be-roles.md]
---

## Scope

- `/admin/login` with 2FA step, `ssr: false` admin layout, guard in `beforeLoad` using `/me`, sidebar showing only permitted areas.
- One reusable `DataTable` (shadcn on Table v9, server-side mode) with page, size, ordering, filters and `q` in validated URL search params.
- TanStack Form + Zod form conventions with a dirty-leave guard; pending, error and not-found components; sonner toasts.

## Acceptance

- [ ] Build check 3: Table v9 manual mode drives server paging from URL params; note the `queryClient.query` versus `ensureQueryData` result.
- [ ] Unauthenticated users land on `/admin/login`; a 403 shows a toast.

## Spec

- [Architecture section 3](../../spec/architecture.md)
