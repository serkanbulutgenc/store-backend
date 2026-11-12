---
title: SEO: metadata, structured data, sitemap, robots, canonicals
repo: store-frontend
slice: 4
type: AFK
status: closed
assignee:
blocked-by: [18-fe-browse.md]
---

## Scope

- Server-rendered titles, descriptions and Open Graph tags from templates; `Product` and `BreadcrumbList` JSON-LD; homepage `Organization` from Store settings (fallback until that exists).
- Canonicals; `noindex, follow` on filtered, sorted and search pages; slug redirects (301) honoured; Django `sitemap.xml` and `robots.txt` through the proxy with the agreed disallow list.

## Acceptance

- [ ] Structured data validates for a sample Product.
- [ ] `robots.txt` disallows `/admin`, `/sepet`, `/odeme`, `/hesabim`, `/siparis-takip` and the API.

## Spec

- [PRD section 2](../../spec/prd.md)
