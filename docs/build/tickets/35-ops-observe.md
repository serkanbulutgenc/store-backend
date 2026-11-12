---
title: Observability: error tracking, uptime and alerts
repo: store-infra
slice: 10
type: HITL
status: closed
assignee:
blocked-by: [33-ops-deploy.md, 29-be-outbox.md]
---

## Scope

- Hosted error tracking for Django and Start with PII scrubbing; JSON logs with Docker rotation; external uptime checks on the storefront and `/health`; alerts by email plus a push channel for downtime.
- Internal alerts: backup age, outbox messages failing after all retries, disk over 80%.

## Acceptance

- [x] A forced exception reaches the tracker without emails or phones in the payload.
- [x] Stopping `api` triggers a downtime alert.

## Spec

- [Architecture section 4](../../spec/architecture.md)
