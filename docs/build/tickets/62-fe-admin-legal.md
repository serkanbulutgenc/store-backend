---
title: Frontend: Dashboard Legal pages editor (Owner)
repo: store-frontend
slice: 15
type: AFK
status: open
assignee:
blocked-by: [60-be-legal-pages.md]
---

## Scope

- Owner-only "Yasal sayfalar" screen (nav entry gated on `manage_legal_pages`): list the seven pages with current version and last edit; open one to edit title and Markdown body with a live preview from the preview endpoint.
- Saving creates the next version after an explicit confirmation that says it publishes immediately and what the version number will be; version history list per page, each version viewable read-only.
- Staff never see the entry and the route refuses them.

## Acceptance

- [ ] The Owner edits a page, previews it, saves, and the Storefront shows the new version.
- [ ] Older versions are listed and readable, never editable.
- [ ] A Staff user cannot open the screen or its route.

## Spec

- ADR 0002 in the backend repo.
