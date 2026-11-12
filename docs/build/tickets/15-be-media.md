---
title: Backend: product media upload, derivatives and delivery
repo: store-backend
slice: 3
type: AFK
status: closed
assignee:
blocked-by: [10-be-catalog.md, 12-be-admin-catalog.md]
---

## Scope

- Product image model (Product, optional Colour, sort order, primary, alt text); upload endpoint accepting JPEG, PNG, WebP up to 15 MB, 12 per Product, 800 px minimum.
- Celery job: keep the original, make WebP derivatives (about 400, 800, 1600 px), strip EXIF, apply orientation; content-hashed names; `media_url()` function; delayed file removal after delete.
- Rules: no publish without an image; last image of a published Product cannot be deleted; audit upload and delete.
- Build check 4: HEIC conversion with the chosen library, otherwise reject with a Turkish message.

## Acceptance

- [x] Processing failure leaves the original usable and the image marked failed (test).
- [x] Alt text defaults to product name and colour.
- [x] HEIC result recorded in the ticket.

## Spec

- [Architecture section 2](../../spec/architecture.md)
- [PRD section 4](../../spec/prd.md)

## Build check 4: HEIC result

Works. `pillow-heif` 1.8.1 installs on Python 3.14 / Pillow 13 (bundled libheif wheel), registers on import in `media._register_heif()`, and tests build real HEIC files: the upload is accepted, the `.heic` original is kept, and WebP derivatives are made. A HEIC with GPS is stored as a JPEG without it. A corrupt HEIC is rejected with a Turkish message. Not tried: a photo straight from an iPhone, and the production image build (check the wheel installs there).

## Notes

- Model is `ProductPhoto` (Oscar already owns `catalogue.ProductImage`); API paths say `images`.
- Delivery headers are Caddy config in store-infra (`caddy/Caddyfile`): the one-year `immutable` header is sent only for files that exist, so a 404 for a derivative still in processing is not cached.
- Deleted files wait `MEDIA_REMOVAL_GRACE_DAYS` (7) in `PendingFileRemoval`; a beat task purges them every 6 hours.
- Originals have GPS location removed on upload (re-encoded only when GPS is present; other metadata stays). HEIC with GPS would be stored as JPEG.
- `media.update` and `reorder` are audited as `photo_edit`.
