---
title: Prototype: admin shell layout (throwaway)
repo: store-frontend
slice: 12
type: HITL
status: closed
assignee:
blocked-by: []
---

## Scope

- Throwaway prototype of the collapsible shadcn sidebar with the three nav groups (Satış, Katalog, Yönetim), breadcrumbs, user menu and mobile menu, with the real labels and a tabbed Settings page.
- Show it to the Owner; record the decision (keep, change, or pick the other layout) in the ticket.

## Acceptance

- [x] The Owner approved a layout, or listed changes that are folded into ticket 42.
- [x] The prototype is deleted or clearly marked throwaway, not shipped.

## Decision (2026-11-12)

The Owner approved the layout as prototyped (grouped collapsible sidebar, breadcrumbs, mobile slide-in, user menu, tabbed Settings). One change, folded into ticket 42: use a suitable icon for every nav item, tab and action across the Dashboard. The prototype lives only on the unmerged branch `prototype/admin-shell` in store-frontend, never on `main`.

## Spec

- [Phase 2 section 3](../../spec/phase-2.md)
