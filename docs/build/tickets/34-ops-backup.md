---
title: Backups and restore drill
repo: store-infra
slice: 10
type: AFK
status: closed
assignee:
blocked-by: [01-infra.md]
---

## Scope

- Nightly compressed `pg_dump`, nightly incremental media sync, weekly provider snapshot, client-side encryption, off-site storage; retention 14 daily, 8 weekly, 6 monthly; keys kept off the VPS.
- Scripted restore into a scratch Compose project; log of drills in the runbook.

## Acceptance

- [ ] A restore from last night's backup produces a working stack.
- [ ] Backup age alert fires when no success in 26 hours.

## Spec

- [Architecture section 4](../../spec/architecture.md)
