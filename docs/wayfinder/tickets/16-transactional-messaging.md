---
title: Grill: transactional email/SMS and staff notifications
label: wayfinder:grilling
status: closed
assignee: claude
blocked-by: []
parent: map.md
---

## Question

Decide which events send messages to Shoppers (order placed with bank details, paid, shipped with tracking, cancelled, verification, password reset) and to Staff (new order, low stock), over which channel (email only vs SMS), via which provider type, templates in Turkish, failure/retry handling, and what is deferred.

## Resolution

Decided in a two-round grilling; all recommendations accepted.

**Channel and provider**
- Email only in phase 1. SMS goes to the fog (needs sender registration, per-message cost and consent rules).
- An SMTP relay or transactional email service behind Django's email backend; vendor not chosen here (EU hosting, price and the KVKK transfer-abroad question go on the launch checklist for the Owner and lawyer). Requirements: SPF, DKIM and DMARC on the shop's domain, sender like `siparis@<domain>`, reply-to the shop.

**Shopper messages**
- Sent: order placed (bank-transfer variant with IBAN, account holder, order number and deadline; COD variant with summary), bank-transfer reminder at 24 h, auto-cancelled for non-payment, marked paid, shipped (carrier and tracking number), cancelled by staff (with reason), return received / refund issued, email verification, password reset, email change verification.
- Not sent: a "delivered" message.
- All are transactional: no unsubscribe link and no marketing content. Marketing, when added, needs consent checks and unsubscribe.

**Staff notifications**
- Email per new order to one or more recipients configured in Store settings; low stock as a daily digest; Owner emailed on account-deletion requests; the Dashboard Home lists (new orders, unpaid transfers near expiry, low stock) stay as decided. No push, SMS or chat integration.

**Sending and failure**
- Outbox pattern: the event writes a message record in the same transaction as the order change; a background worker sends with retries (about 5 attempts over about 24 h). Order placement never fails because email is down. Failed messages are visible in the Dashboard with the error and can be resent by Staff or Owner. Needs a worker process (cookiecutter-django ships Celery) in the deployment ticket.
- Provider bounce and complaint webhooks mark a message `bounced`; Order detail shows an "e-posta ulasmadi" warning so Staff can phone the Shopper. No suppression list in phase 1.

**Templates and log**
- Turkish only, HTML plus plain-text versions, in the repo. Shop name, bank details, contact and signature come from Store settings. Footer carries the shop's name, address and contact. No template editor in the Dashboard.
- Every message is kept (recipient, template, time, status, rendered body), linked to the Order and shown on Order detail. Never purged in phase 1, consistent with no hard deletes.

**Content safety**
- Order email shows IBAN, account holder and order number only; the signed guest order link expires after 30 days (order tracking by order number plus email or phone still works); links never contain phone or full address.
- Checkout uses a single email field with a format check and a typo hint for common domains. Staff can correct an Order's email on Order detail; the change is audit-logged and resends the order email.
