# Aura Tekstil online store: phase 4

Status: scoped with the Owner on 2026-11-23 in a grilling session. Builds on [prd.md](prd.md), [architecture.md](architecture.md), [phase-2.md](phase-2.md) and [phase-3.md](phase-3.md); where this file is silent, those stand. Items marked **(default)** were not asked about and are the author's choice; the Owner may overrule them. New vocabulary (Campaign page, Banner, Slot, Approval, Pending revision, Contact thread) goes into `GLOSSARY.md` when the phase is scheduled.

## 1. Goals and non-goals

Phase 4 gives Staff room to prepare marketing and customer-service content while the Owner keeps control of what goes live. Four features sharing one approval rule:

1. **Campaign pages**: an editorial page per Campaign, with a cover and a markdown description, listed under a public menu.
2. **Banners and promotion messages**: several images and messages in four fixed places on the site.
3. **Legal page URLs**: the Owner changes the slug of each existing Legal page.
4. **Contact service**: a Shopper form, a shared inbox for Owner and Staff, and Staff reply drafts the Owner approves.

**Not in phase 4:** new Legal pages, an in-site Shopper inbox or account view of messages, email notifications for pending approvals, pop-ups, a free-form page builder, CAPTCHA, SMS, and everything already deferred in phase 3 (online card payments, marketing sends, analytics, carrier API, POS stock sync, reorder).

## 2. The approval rule (shared)

Applies to Campaign pages, Banners and Staff reply drafts.

- **States.** Draft, Pending approval, Published. Staff create and edit drafts and submit them. Only the Owner approves, rejects (with a short reason Staff see on the draft), publishes or unpublishes. The Owner may also create and publish directly.
- **Editing something live.** The live version stays up unchanged. A Staff edit is saved as a **Pending revision** that waits for the Owner. Approving replaces the live version; rejecting discards the revision. Shoppers never see an unreviewed change.
- **Revision history.** Each approved version is kept, so the Owner can see what changed against the live version and what was live before.
- **Queue.** One "Waiting for approval" list in the Dashboard, grouped by type, showing the submitter and time, with a count badge on the menu item. The Owner opens an item, sees the difference from the live version, then approves or rejects. **No email notifications** in phase 4; a digest may follow once SPF, DKIM and DMARC are in place.
- **Audit.** Submit, approve, reject, publish and unpublish are written to the Audit log.

## 3. Campaign pages

- **Two linked things.** The existing discount **Campaign** stays an Owner-only price rule (a percentage, a schedule, the Products, Categories and Collections it covers); it is unchanged. A new **Campaign page** is the editorial side: Staff draft it, the Owner approves it.
- **Fields.** Title, slug, cover image (with alt text), a markdown description (the existing markdown renderer and its sanitising), optional start and end dates, and an optional link to one discount Campaign.
- **Link.** Only the Owner can link or unlink a discount Campaign, since that decides what the page promises. A linked page lists the discounted Products and shows the Campaign's dates; it follows the Campaign's own active schedule. An unlinked page is purely editorial. **(default)**
- **Public pages.** `/kampanyalar` lists every published Campaign page, newest first, with its cover and title; each opens `/kampanyalar/<slug>`. A page past its end date drops off the list and shows an "ended" notice at its URL. **(default)**
- **Menus.** Nothing is added to the menus automatically. The **Owner** adds them in the existing menu builder (header and footer): a new "Kampanya sayfası" target type for one Campaign page, and a new Built-in page "Kampanyalar" for the `/kampanyalar` list. Like the other targets, an item follows its page by reference, and the builder shows the usual warning if the page is unpublished or missing. Menu editing stays Owner-only; Staff cannot place a page in a menu.
- **Rule.** Staff can create and edit Campaign pages but not publish them; publishing follows section 2.

## 4. Banners and promotion messages

- **Slots.** A fixed set, chosen per Banner and defined in the storefront (no free placement):
  1. **Announcement bar**: a text-only strip above the header for promotion messages; several messages rotate.
  2. **Home hero**: a large carousel at the top of the homepage with several images.
  3. **Home mid-page**: one or two wide banners between homepage sections.
  4. **Category page top**: a banner above the listing, optionally tied to one Category.
- **Fields.** Slot, image (plus an optional mobile image) and alt text, or message text for the announcement bar, an optional link, a start and end date, and an order within the slot.
- **Rotation.** The hero rotates automatically with controls, pausing on hover or focus and honouring reduced motion; announcement messages rotate slowly, one at a time. **(default)**
- **Rule.** Staff draft Banners; the Owner approves the slot and content (section 2). A Banner outside its dates is not shown.
- **Out.** Pop-ups, and any slot in the basket or checkout.

## 5. Legal page URLs

- **Scope.** The Owner changes the URL slug of each of the seven existing Legal pages. Nothing else: no new Legal pages.
- **Keys unchanged.** A Legal page keeps its internal key, so Consent records, the (page, version) pairs they point at, and version history are untouched.
- **Redirects.** The old URL redirects permanently (301) to the new one. The Owner sees the redirects and cannot remove one while it points at a live page.
- **Menus.** The Owner places Legal pages in the header and footer through the existing menu builder, which already has a Legal page target. Items follow the page by reference, so a changed slug needs no menu edit.
- **Validation.** Lowercase letters, digits and hyphens; unique; not a reserved path (`admin`, `kampanyalar`, `iletisim`, or anything the app already routes).
- **Permission.** Owner-only, written to the Audit log. Editing the text keeps its current versioning.

## 6. Contact service

- **Form.** Open to Guests and Customers: category, name, email, an optional order number, and a message. Abuse limits are a per-IP rate limit and a hidden honeypot field (no CAPTCHA).
- **Categories.** An Owner-managed list seeded with Contact, Request, Order question, Return and Complaint. The Owner can add, rename or deactivate; one with threads cannot be deleted.
- **Thread.** The Shopper's message, Staff drafts, approvals and sent replies in order, with a status (New, In progress, Answered) and an assignee. Owner and Staff both see every thread.
- **Replies.** Staff write a draft reply. The Owner approves it as written, edits it first, or rejects it with a note; only then is it emailed through the existing outbox. The Owner can reply directly without a second approval.
- **Shopper side.** An automatic acknowledgement email, then the approved reply by email. No in-site inbox, and no view of drafts or status.
- **Search.** Postgres full-text search over name, email, order number, category, status, date and message text, for Owner and Staff.
- **Retention.** A thread is kept for 3 years after its last activity, then a scheduled job anonymises it (name, email and message text removed, counts kept). An account deletion request anonymises that Shopper's threads at once.
- **Order link.** An order number that matches an Order links the thread to it. **(default)**

## 7. Order of work

1. **The approval rule**: Pending revision model, queue endpoint and Audit entries, then the Dashboard queue and badge. Everything else depends on it.
2. **Legal page URLs**: small and independent; ships first or in parallel.
3. **Campaign pages**: model and API, Dashboard editor, the menu builder target and Built-in page, then `/kampanyalar`.
4. **Banners**: model and API, Dashboard editor, then the four slots on the storefront.
5. **Contact service**: model, form API with abuse limits, thread and search, reply drafts with approval, the Dashboard inbox, then the storefront form, retention job and anonymisation.

## 8. Launch checklist additions

- One privacy-notice sentence for contact messages (what is kept, 3 years, anonymised afterwards), approved with the lawyer's text in store-infra #90.
- SPF, DKIM and DMARC records in place before contact replies go out; they are still `TODO` in the runbook.
- Add each item to `docs/launch/checklist.md` in store-infra when the phase is scheduled.

## 9. Open questions and build-time checks

- Whether the Owner's direct reply and direct publish also create an approval entry, or only an Audit entry. Assumed Audit only.
- Image upload limits and formats for covers and Banners; reuse the Description and Brand logo uploads.
- Reserved-path list for Legal slugs must be read from the real router at build time.
- Replies reuse the outbox `Message`; confirm it can hold a reply linked to a Contact thread without a new field on `Order`.
- Whether Banner images need responsive `srcset` variants generated on upload.
