---
title: Backend: allauth app mode, token bridge, CSRF endpoint, guest auth class
repo: store-backend
slice: 2
type: AFK
status: closed
assignee: claude
blocked-by: [06-be-api.md]
---

## Scope

- Mount allauth headless in app mode at `/_allauth/app/v1/`; email verification mandatory; rate limits on login, sign-up and password reset.
- Middleware before `BasketMiddleware` reading `X-Session-Token` and setting `request.user`.
- `GET /api/store/csrf` and a guest auth class with explicit CSRF for basket and checkout endpoints; token-authenticated endpoints skip CSRF.
- Oscar guest-checkout settings enabled; basket cookie lifetime 14 days, `Secure` in production.

## Acceptance

- [x] Build check 1: with a token, a guest basket merges into the customer basket on login and customer baskets load unchanged (test). If it fails, switch to allauth browser mode and record the change.
- [x] A guest basket endpoint rejects a missing CSRF token and accepts a valid one.

## Spec

- [Architecture section 2](../../spec/architecture.md)
- [Architecture section 6](../../spec/architecture.md)
