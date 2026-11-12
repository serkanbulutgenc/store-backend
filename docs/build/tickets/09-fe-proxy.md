---
title: Frontend: API proxy route, session cookie and generated clients
repo: store-frontend
slice: 2
type: AFK
status: closed
assignee:
blocked-by: [03-fe-scaffold.md, 06-be-api.md, 07-be-auth.md]
---

## Scope

- Catch-all `/api/**` route that attaches the session token and guest basket cookie and forwards to Django; server loaders use the same helper.
- HttpOnly, Secure, SameSite=Lax session cookie; a 401 clears it.
- `pnpm api:sync` copying specs from the sibling backend into `api/specs/` and generating `.d.ts`; two `openapi-fetch` clients; `ApiError` mapping from the envelope.
- Root `beforeLoad` loading `/me` into router context and a `can()` helper.

## Acceptance

- [ ] Build check 2: one guest basket and one login survive an SSR hop and a browser call (test). If Start cannot set cookies in the proxy, switch to the documented fallback.
- [ ] Types fail to compile when a committed spec changes incompatibly.

## Spec

- [Architecture section 3](../../spec/architecture.md)
