---
title: Spike: Oscar headless viability and SSR cookie forwarding
label: wayfinder:task
status: closed
assignee: claude
blocked-by: []
parent: map.md
---

## Question

Settle, with a throwaway proof (AFK, agent-driven, discarded afterwards), the facts the research left unverified, since the API boundary and frontend architecture decisions hang on them:

1. Does django-oscar 4.2.x work on a fresh cookiecutter-django project (its `User` has a single `name` field, no first/last name; Python 3.14 / current Django)?
2. Can Oscar run without its dashboard apps and templates in INSTALLED_APPS without breaking startup?
3. Can a TanStack Start server function / loader forward the allauth session cookie, CSRF token and the `oscar_open_basket` cookie to a separate Django Ninja API, and set them back to the browser, so SSR and browser calls share one guest basket and one login session?
4. Does the Ninja `SessionAuth` (CSRF on) work with that flow, or is `X-Session-Token` needed?

Record what worked, what didn't and the exact settings needed. Do not keep the spike code in either repo.

## Resolution

Throwaway proof, run 2026-11-09 in a scratch dir (cookiecutter-django output with `username_type=email`, Django 6.0.10, django-oscar 4.2.1, django-ninja 1.8.0, django-allauth 65.21.1 incl. `allauth.headless`, CPython 3.14, Postgres 17). Nothing kept in either repo.

**1. Oscar on cookiecutter's User: works.** All Oscar migrations apply and `makemigrations --check` is clean against the single-`name`, email-as-username `users.User`. Needed: `from oscar.defaults import *`, Oscar app list from `oscar.INSTALLED_APPS`, `django.contrib.flatpages`, `widget_tweaks`, `haystack` (with `HAYSTACK_CONNECTIONS` set to the simple backend), `treebeard`, `django_tables2`, and `oscar.apps.basket.middleware.BasketMiddleware`. Cookiecutter's `sites` migration is Postgres-only (`django_site_id_seq`), so SQLite is not usable; use Postgres even for dev and tests.

**2. Oscar without its dashboard: works, with a shim.** Dropping every `oscar.apps.dashboard.*` app fails: `oscar.config.Shop.ready()` needs the `dashboard` app label, and Oscar's `dashboard_tags` templatetag module is scanned by Django and imports `dashboard.nav`. Fix: register a subclass of `oscar.apps.dashboard.apps.DashboardConfig` with a no-op `ready()` (it does not need the sub-dashboards). Do not include Oscar's URLs; the Ninja API is the only entry point. `oscar.config.Shop` can stay.

**3. Cookie forwarding through an SSR hop: works at the HTTP level.** A plain Node forwarder (Cookie in, every Set-Cookie out, `Origin` set) kept one guest basket and one login across both legs: guest `oscar_open_basket` basket persisted across calls; allauth browser-client login set `sessionid`; on login Oscar merged the guest basket into a new user basket (2 items kept). Caveats: this proves the HTTP mechanics, not TanStack Start's own API (`getRequest` / setting response headers in a server function); that still needs a quick check in the first frontend ticket. `CSRF_COOKIE_HTTPONLY = True` (cookiecutter default) means browser JS cannot read `csrftoken`; fetch the token from an endpoint (`get_token`) and send it as `X-CSRFToken`. `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` must include the frontend origin.

**4. Ninja auth: session + CSRF works; token client needs care.**
- `django_auth` (SessionAuth) enforces CSRF: POST without `X-CSRFToken` gives 403, with it 200; unauthenticated GET gives 401.
- Ninja 1.8 has no `NinjaAPI(csrf=...)` argument. Endpoints with `auth=None` (guest basket add) are NOT CSRF-protected. Decide whether guest mutating endpoints accept that (they act on the signed basket cookie only) or add an explicit CSRF check.
- `X-Session-Token` (allauth app client) works through `allauth.headless.contrib.ninja.security.x_session_token_auth`, but the user lands in `request.auth`, not `request.user`, so Oscar's `BasketMiddleware` sees an anonymous user. Prefer the browser (session cookie) client for the SSR frontend and skip the token client.
- Django's admin-email error logger crashes with `NoReverseMatch: 'catalogue' is not a registered namespace` (it reprs Oscar's `reverse_lazy` settings) when Oscar URLs are not mounted, which hides the real exception. Use another log handler or mount a stub, and watch for it in production.

**Net for downstream tickets:** headless Oscar is viable. API boundary can assume session-cookie auth with CSRF, a shared guest basket cookie, and Oscar's models used via a service layer. Catalogue model can assume Oscar's `Product`/`ProductClass`/`StockRecord` on the existing User.
