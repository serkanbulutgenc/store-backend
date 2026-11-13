---
title: Backend: Legal pages as immutable versions, Owner edit API, search
repo: store-backend
slice: 15
type: AFK
status: closed
assignee:
blocked-by: []
---

## Scope

- Model per [ADR 0002](../../adr/0002-legal-pages-are-immutable-versions-in-the-database.md): one row per Legal page and version (page key from `LEGAL_PAGES`, integer version, title, Markdown body, created by, created at). A saved row is never updated or deleted; an edit inserts the next version.
- A page's current version is its highest number. Replace `StoreSettings.legal_versions` with that, so checkout's `_check_legal_versions` and the Consent record keep working with the same string versions ("1", "2"). A data migration seeds each page at exactly the version number in force from Markdown copied into `store_backend/shop/legal_seed/` (the frontend repo's files, so no command or manual step is needed on deploy); a second migration drops the old field.
- Store API (public): list current pages (key, slug, title, version), get one current page, get one specific version (so a Consent record can be shown), and search the current pages (PostgreSQL full-text search with the Turkish configuration over title and body, ranked, with a short excerpt).
- Admin API: list pages with their latest version, list a page's versions, create the next version (title and body). The preview happens in the browser with the Storefront's own renderer, so there is no preview endpoint. New Owner-only permission `manage_legal_pages` (migration adds it to the Owner group); the guard test's Owner-only set is updated in the same change.
- Audit log entry for every new version (who, when, page, version).
- The store API cache or the frontend must pick up a new version without a deploy.

## Acceptance

- [x] A new version appears on the Storefront without a deploy and the page's current version rises by one.
- [x] A Consent record placed on version N still resolves to version N's exact text after version N+1 exists.
- [x] A checkout form carrying an older version of a page than the current one is refused as before.
- [x] Staff get 403 on every admin Legal page operation; the Owner-only guard test lists them.
- [x] Search finds a word in a body ("cayma"), ignores older versions and marks matches with « ». (Turkish stemming comes from PostgreSQL's `turkish` configuration; İ/ı casing follows the database locale and is not asserted.)
- [x] No endpoint can change or delete a saved version.

## Spec

- [PRD Legal pages](../../spec/prd.md); [ADR 0002](../../adr/0002-legal-pages-are-immutable-versions-in-the-database.md); CONTEXT.md terms Legal page and Legal page version.
