---
title: Reviews: verified-buyer ratings, moderation queue, basic markdown and emoji
repo: store-backend + store-frontend
slice: 16
type: AFK
status: open
assignee:
blocked-by: [67-favorites.md]
---

## Scope

- Own model `Review` (Shopper, Product, stars 1-5, body, status, timestamps, moderated by and at), not Oscar's reviews app ([ADR 0003](../../adr/0003-attributes-favorites-reviews-are-own-models.md); moderation policy in [ADR 0004](../../adr/0004-reviews-are-moderated-verified-buyer-ratings.md), both written here). No title field.
- Eligibility: the Shopper has a delivered Order line for that Product. A fully returned line removes eligibility. One review per Shopper per Product; the Shopper may edit or delete it. An edit of text sends it back to moderation.
- Publishing: stars without text publish immediately; any body text (including emoji only) waits in the moderation queue. Owner and Staff moderate (approve, reject, delete; correctable, so no extra permission). No shop reply in this phase. Audit log entries.
- Body is basic markdown: bold, italic, strikethrough, paragraphs, line breaks, bulleted lists. No HTML, headings, tables, images or clickable links; bare URLs stay plain text. Max 2000 characters. The server stores the raw source and rejects HTML and strips links and images. Render with a small markdown-to-React-elements parser (no `dangerouslySetInnerHTML`); the moderation queue shows rendered text next to the source.
- Emoji are stored as Unicode. The form converts a small common `:shortcode:` set on submit. No picker.
- Display name "Ayşe K." (first name and last initial). Product page shows average, count and the published list; listing cards show average and count. No rating sort and no helpful votes yet.
- Account deletion removes or anonymises the Shopper's reviews (decide with the existing deletion rules, record it in ADR 0004).
- Docs: CONTEXT.md term Review; prd.md and phase-2.md; ADRs 0003 and 0004.

## Acceptance

- [ ] A Shopper without a delivered line, or whose line was fully returned, cannot review; the API says why.
- [ ] A star-only rating shows at once and moves the average; a text review is invisible until approved.
- [ ] Editing the text of an approved review hides it again until re-approved.
- [ ] `<script>`, links, images and headings never reach the page; bold, italic, strikethrough, lists and emoji render, including `:smile:` converted to 😄.
- [ ] Staff and Owner can approve, reject and delete; the queue shows rendered text and source; all are audited.
- [ ] Average and count on product page and cards match the published reviews.

## Spec

- Decisions of 2026-11-13 Admin features interview; consumer-law reason for moderation recorded in ADR 0004.
