---
title: Grill: dashboard scope, roles and permissions
label: wayfinder:grilling
status: closed
assignee: claude (serkanbulutgenc@gmail.com session)
blocked-by: [07-guest-and-accounts.md, 08-api-boundary.md]
parent: map.md
---

## Question

Decide: the dashboard's phase 1 screens (catalog, stock, orders, customers, suppliers/brands, users), owner vs staff capabilities, how permissions map to Django groups/permissions and are enforced in the API and reflected in the UI, audit trail needs.

## Resolution

Decided with the user (all recommended answers accepted). Glossary updated in `/CONTEXT.md`.

1. **Screens, phase 1.** Orders (list/detail, mark paid, cancel with reason, ship with carrier and tracking, deliver, return, refund); Products with Variants, photos, prices, status; Stock (manual adjustments with reason, low-stock list); Categories, Brands, Suppliers, Colour list; Customers (read-only); Settings (shipping fee and threshold, COD fee, low-stock default, bank details); Staff users; Home (new orders, unpaid transfers near expiry, low stock; no charts). Out: reports, discounts, CMS pages, label printing, messaging inbox.
2. **Owner-only.** Settings, Staff users, Colour list, cost price and Supplier data, archive/unarchive, recording refunds, cancelling paid Orders, account-deletion requests. Staff role: everything else, including marking paid.
3. **Permission model.** Two fixed Django groups (Owner, Staff) seeded by migration, not editable in the UI. Endpoints check named permissions, so custom roles can come later.
4. **Enforcement.** Each admin endpoint declares one permission; missing permission returns 403 in the error envelope. `GET /api/admin/me` returns the permission list; the UI hides controls from it (UX only). Owner-only fields (cost price) are omitted from responses for Staff and optional in the admin spec.
5. **Audit log.** Business-event log, not full field history: mark paid, cancel, refund, ship, stock adjustment, price change, user/role change, settings change, login. Actor, time, target, short before/after summary.
6. **Staff accounts.** Owner invites by email; one user table with `is_staff` (a Staff member who also shops uses a separate email); deactivate only. TOTP 2FA (allauth MFA) required for Owner, optional for Staff; same allauth app-mode login with tighter rate limits.
7. **Personal data.** Staff see Customer name, phone, email, addresses and order history; no export. Account-deletion requests land in the Owner's queue and the anonymisation action is logged. Staff may edit an Order's shipping address and phone only while `new` or `preparing`, audited. No impersonation. CSV export for the accountant deferred to the e-Arsiv work.
