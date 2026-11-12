---
title: Backend: outbox transport and scheduler seams
repo: store-backend
slice: 14
type: AFK
status: closed
assignee:
blocked-by: []
---

## Scope

- `Transport.send(message)` with an SMTP adapter and an in-memory adapter; a scheduler seam for kicking delivery (Celery and inline). Remove the lazy-import cycle between outbox and checkout/tracking by moving order rendering context to its own module.
- Retry and backoff tests use the in-memory transport instead of patching Django's mail class.

## Acceptance

- [ ] Behaviour is unchanged: outbox and notification tests pass with only their fakes swapped.
- [ ] No test patches `EmailMultiAlternatives` or `outbox.render`.

## Spec

- [Phase 2 section 6](../../spec/phase-2.md)
