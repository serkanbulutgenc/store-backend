---
title: Frontend: Storefront Legal pages from the API, with search
repo: store-frontend
slice: 15
type: AFK
status: open
assignee:
blocked-by: [60-be-legal-pages.md]
---

## Scope

- `yasal/$slug` loads the current version from the Store API instead of `content/*.md`; footer and checkout links keep their slugs. Checkout sends the versions it was shown, read from the API.
- Search box on the Legal pages area (a `yasal` index with all pages and a search field, results with excerpts linking to the page).
- Remove `src/features/legal/content/*.md` and the version constants once the API is the source; keep the small Markdown renderer (no raw HTML).
- Pages stay indexable; titles and the version line still render.

## Acceptance

- [ ] Every Legal page renders the API's current text and version; an unknown slug is a 404.
- [ ] Searching a word returns the matching pages with excerpts, none for older versions.
- [ ] Checkout still records the versions shown; a version bump between page load and submit shows the existing "page changed, reload" path.
- [ ] No Markdown content or version number is left hard-coded in the repo.

## Spec

- [PRD Legal pages](../../../../store-backend/docs/spec/prd.md); ADR 0002 in the backend repo.
