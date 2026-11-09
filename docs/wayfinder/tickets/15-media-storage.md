---
title: Grill: product media storage, processing and delivery
label: wayfinder:grilling
status: closed
assignee: claude
blocked-by: []
parent: map.md
---

## Question

Decide how product images are uploaded in the Dashboard, stored (VPS disk vs object storage), processed (resizing, formats, limits), served (CDN or Caddy), and linked to Products/Variants (per colour, ordering, alt text), given a single VPS on Docker Compose and 1-5 staff taking photos on phones.

## Resolution

Decided in a two-round grilling; all recommendations accepted.

- **Storage:** originals and derivatives on the VPS disk on the shared volume, backed up with the database (backup scope belongs to the deployment ticket). All URLs go through one `media_url()` function so a later move to object storage stays possible.
- **Processing:** on upload, a background job keeps the original untouched and generates fixed WebP derivatives (about 400, 800 and 1600 px wide); EXIF stripped and orientation applied; no image-proxy service. The storefront uses `srcset` over those sizes. A failed job leaves the original usable.
- **Rules:** JPEG, PNG, WebP; HEIC converted on upload if library support is confirmed during the build, else rejected with a Turkish message (unverified); 15 MB max; up to 12 images per Product; minimum 800 px on the long edge; multi-file upload.
- **Attachment:** an image belongs to a Product with an optional Colour tag (from the fixed list), sort order, primary flag and alt text. Colour-less images are shared; the storefront filters the gallery by selected colour and falls back to shared images. Never attached to size Variants.
- **Alt text and names:** alt text defaults to product name and colour, optionally overridden; filenames are generated (UUID plus size), never user-supplied.
- **Delivery:** Caddy serves the volume; content-hashed immutable URLs with `Cache-Control: public, max-age=31536000, immutable`; a replacement gets a new URL; deleted files are removed from disk after a grace period.
- **Publishing:** a Product cannot go from draft to published without at least one image; the storefront placeholder is only a defensive fallback; the Dashboard has a data-quality filter for published Products without images.
- **Permissions:** Staff and Owner can upload, reorder, tag colour and delete; deleting the last image of a published Product is blocked; upload and delete are audit-logged business events.
- **Dashboard UX:** drop zone that also opens camera/gallery on mobile; per-file progress; instant local thumbnails with an "isleniyor" marker until derivatives are ready; drag to reorder; click to set primary; colour dropdown per image; this screen stays usable at phone width, the rest of the Dashboard is desktop-first.
- **Not in phase 1:** bulk catalog import (data plus photos); products are created one by one.
