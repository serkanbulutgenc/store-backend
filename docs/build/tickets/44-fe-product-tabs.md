---
title: Frontend: product editor as tabs
repo: store-frontend
slice: 12
type: AFK
status: closed
assignee:
blocked-by: [42-fe-shell.md]
---

## Scope

- Tabs Genel, Fiyat, Varyantlar, Fotoğraflar (photos managed in place, replacing the separate photos route link). Owner-only fields (cost price, supplier, archiving) stay hidden from Staff.

## Acceptance

- [x] All existing product edit flows work through the tabs; the old photos route redirects to the Fotoğraflar tab.
- [x] Unsaved changes are kept when switching tabs.

## Spec

- [Phase 2 section 3](../../spec/phase-2.md)
