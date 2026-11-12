---
title: Backend: Oscar as an engine, without its dashboard
repo: store-backend
slice: 2
type: AFK
status: closed
assignee: claude
blocked-by: [02-be-scaffold.md]
---

## Scope

- Settings per the spike: `oscar.defaults`, Oscar app list minus every `oscar.apps.dashboard.*`, a `DashboardConfig` subclass with a no-op `ready()`, flatpages, widget_tweaks, haystack simple backend, treebeard, django_tables2, `BasketMiddleware`, `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`.
- Do not include Oscar URLs or templates.

## Acceptance

- [x] All Oscar migrations apply on the email `User`; `makemigrations --check` is clean.
- [x] App starts with the dashboard apps removed (test).
- [x] No Oscar URL is routed.

## Spec

- [Architecture section 2](../../spec/architecture.md)
