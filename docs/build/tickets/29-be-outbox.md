---
title: Backend: message outbox, worker, SMTP and bounce handling
repo: store-backend
slice: 8
type: AFK
status: closed
assignee:
blocked-by: [02-be-scaffold.md, 05-be-oscar.md]
---

## Scope

- Message model written in the same transaction as the triggering change; Celery sender with about 5 retries over 24 h; SMTP relay backend; bounce and complaint webhook marking `bounced`; resend action; Turkish HTML and text template engine fed by Store settings.
- Order detail API exposes the message log (recipient, template, time, status, rendered body).

## Acceptance

- [ ] Order placement succeeds when the SMTP server is down (test) and the message is delivered when it returns.
- [ ] A permanently failing message stays visible with its error.

## Spec

- [Architecture section 2](../../spec/architecture.md)
