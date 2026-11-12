---
title: Playwright: guest COD checkout end to end
repo: store-infra
slice: 10
type: AFK
status: closed
assignee:
blocked-by: [04-e2e.md, 23-fe-checkout.md, 30-be-notify.md]
---

## Scope

- Extend the harness: browse, add to basket, place a COD order as a guest, see the confirmation, find the order in the Dashboard. Runs against the Compose stack in CI.

## Acceptance

- [ ] The test passes in CI and fails if the SSR cookie hop breaks.

## Spec

- [Architecture section 4](../../spec/architecture.md)
