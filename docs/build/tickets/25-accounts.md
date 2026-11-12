---
title: Accounts: sign-up, verification, login, reset and account area
repo: store-frontend
slice: 6
type: AFK
status: closed
assignee:
blocked-by: [09-fe-proxy.md, 07-be-auth.md, 22-be-checkout.md]
---

## Scope

- Sign-up, create-account offer on confirmation, email verification before first login, login/logout, password reset, change email (re-verified) and password; `_customer` guard.
- Profile, address book (up to 5, one default, prefilled at checkout), order history and detail, marketing consent toggle, account-deletion request to the Owner queue.
- Claiming: attach guest Orders when the verified email matches (past and new); never re-assign an owned Order.

## Acceptance

- [ ] A guest order is attached after the matching email is verified (test).
- [ ] Deletion request appears in the Owner queue; anonymisation keeps Orders with snapshot name and billing address.

## Spec

- [PRD section 2](../../spec/prd.md)
