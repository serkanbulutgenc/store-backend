---
title: Frontend: new admin shell with grouped sidebar and breadcrumbs
repo: store-frontend
slice: 12
type: AFK
status: closed
assignee:
blocked-by: [41-fe-shell-prototype.md]
---

## Scope

- Install the shadcn components the shell and tabs need (sidebar, sheet, tabs, breadcrumb, separator, dropdown-menu, label, textarea, card) and replace `admin-shell.tsx` and `nav.ts` with the approved layout.
- Grouped navigation, collapsible sidebar, breadcrumbs on every screen (replacing ad-hoc back links), mobile slide-in menu, user menu with sign-out. Permission gating unchanged.
- Icons (lucide-react): a suitable icon on every sidebar item, group, user-menu entry, tab and primary action across the Dashboard (Owner request on the prototype). The collapsed rail shows the icon, not two letters.
- Tables scroll on small screens; no per-screen responsive redesign.

## Acceptance

- [x] Every existing admin route renders inside the new shell; Staff still do not see Owner-only items.
- [x] Keyboard-navigable sidebar; `pnpm typecheck`, lint and unit tests green; the Playwright admin smoke path passes.

## Spec

- [Phase 2 section 3](../../spec/phase-2.md)
