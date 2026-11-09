---
title: Research: allauth + Ninja auth for an SSR TanStack Start frontend
label: wayfinder:research
status: closed
assignee:
blocked-by: []
parent: map.md
---

## Question

Establish facts: allauth headless API (app vs browser clients), session vs token auth with django-ninja, CSRF/CORS/cookie handling when the SSR frontend and API are on different origins or subdomains, how to carry an anonymous Oscar basket/guest session across requests from SSR and browser, email/password signup+login, email verification. Write findings to research/02-headless-auth.md with source links.

## Resolution

Findings: `../research/02-headless-auth.md`.
- allauth headless has two clients: `browser` (Django session cookie + CSRF) and `app` (`X-Session-Token` header = session key, csrf-exempt); email/password signup/login/verify are `/_allauth/{client}/v1/auth/*` with 401 pending-flow responses.
- django-ninja `SessionAuth` (cookie, CSRF enforced by default) works with the browser client; `APIKeyHeader` does no CSRF check.
- Cross-origin needs CORS (credentials, `x-session-token` header) plus `CSRF_TRUSTED_ORIGINS`; subdomain sharing uses `CSRF_COOKIE_DOMAIN`/`SESSION_COOKIE_DOMAIN`.
- Oscar anonymous basket is an HttpOnly signed cookie `oscar_open_basket` (7 days), not the Django session; merged into the user's basket on login via middleware. Gaps are listed in section 6.
