---
title: Backend: Legal pages as immutable versions, Owner edit API, search
repo: store-backend
slice: 15
type: AFK
status: open
assignee:
blocked-by: []
---

## Scope

- Model per [ADR 0002](../../adr/0002-legal-pages-are-immutable-versions-in-the-database.md): one row per Legal page and version (page key from `LEGAL_PAGES`, integer version, title, Markdown body, created by, created at). A saved row is never updated or deleted; an edit inserts the next version.
- A page's current version is its highest number. Replace `StoreSettings.legal_versions` with that, so checkout's `_check_legal_versions` and the Consent record keep working with the same string versions ("1", "2"). A data migration seeds version 1 (and returns_withdrawal version 2) from the existing Markdown; the files live in the frontend repo, so provide a management command `load_legal_pages <dir>` that loads them and is safe to re-run.
- Store API (public): list current pages (key, slug, title, version), get one current page, get one specific version (so a Consent record can be shown), and search the current pages (PostgreSQL full-text search with the Turkish configuration over title and body, ranked, with a short excerpt).
- Admin API: list pages with their latest version, list a page's versions, create the next version (title and body), and a Markdown preview endpoint that renders without saving. New Owner-only permission `manage_legal_pages` (migration adds it to the Owner group); the guard test's Owner-only set is updated in the same change.
- Audit log entry for every new version (who, when, page, version).
- The store API cache or the frontend must pick up a new version without a deploy.

## Acceptance

- [ ] A new version appears on the Storefront without a deploy and the page's current version rises by one.
- [ ] A Consent record placed on version N still resolves to version N's exact text after version N+1 exists.
- [ ] A checkout form carrying an older version of a page than the current one is refused as before.
- [ ] Staff get 403 on every admin Legal page operation; the Owner-only guard test lists them.
- [ ] Search finds a word in a body ("cayma"), ignores older versions, and handles Turkish casing (İ/ı).
- [ ] No endpoint can change or delete a saved version.

## Spec

- [PRD Legal pages](../../spec/prd.md); [ADR 0002](../../adr/0002-legal-pages-are-immutable-versions-in-the-database.md); CONTEXT.md terms Legal page and Legal page version.
