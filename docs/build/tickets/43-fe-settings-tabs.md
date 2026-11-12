---
title: Frontend: Settings as tabs
repo: store-frontend
slice: 12
type: AFK
status: closed
assignee:
blocked-by: [42-fe-shell.md]
---

## Scope

- Split the Settings form into tabs: Kargo ve ücretler, Banka bilgileri, Bildirimler, Ana sayfa afişi, Satıcı bilgileri; keep the read-only legal versions block visible on its own tab.
- One form, one zod schema and one Kaydet button across tabs; per-field errors, and a tab shows an error marker when a field in it is invalid; replace the raw textarea with the shadcn one.

## Acceptance

- [x] Editing fields in two tabs and saving once sends a single PATCH with both changes.
- [x] An invalid field in a hidden tab blocks the save and marks that tab.

## Spec

- [Phase 2 section 3](../../spec/phase-2.md)
