---
title: Grill: guest checkout, optional accounts and merging
label: wayfinder:grilling
status: closed
assignee: claude (serkanbulutgenc@gmail.com session)
blocked-by: [01-research-oscar-fit.md, 02-research-headless-auth.md]
parent: map.md
---

## Question

Decide: guest identity (email/phone captured at checkout), how guests track an order, basket persistence for anonymous users, what happens when a guest later signs up with the same email (claiming past orders, merging basket), address book, email verification requirement. Update CONTEXT.md.

## Resolution

Decided with the user (all recommended answers accepted, two rounds):

1. **Guest data at checkout.** Mandatory: full name, email, phone, shipping address (il, ilcce, mahalle, street line; postal code optional). Not collected in phase 1: TC kimlik no, date of birth, corporate invoice fields (belong to the e-Arsiv work). Billing = shipping unless the "different billing address" box is ticked.
2. **Guest order tracking.** Both: confirmation page and email show the order number, bank details for transfers, and a signed link to the order page; a rate-limited "Track order" page takes order number plus the email or phone on the order. Both show status, items, totals and tracking number.
3. **Anonymous basket.** Keep Oscar's signed cookie basket; lifetime raised from 7 to 14 days; cookie `Secure` in production. Accept Oscar's merge on login (guest lines added to the account basket, duplicate lines not summed). Baskets never reserve stock; only placed orders do.
4. **Accounts and login.** Sign-up page, plus a "create account" offer on the order confirmation page (set a password only), never forced in checkout. Email + password login only (no username, no social login); password reset by email. Email verification mandatory before first login; guests never need verification.
5. **Address book and consent.** Accounts have up to 5 addresses with one default, prefilled at checkout; guests have none. Checkout has two separate unticked items: mandatory acknowledgement of the distance-sales contract and pre-information (privacy notice linked), and optional marketing consent for email/SMS, stored with a timestamp. Exact legal wording is for the lawyer.
6. **Linking orders to accounts.** Past guest orders attach to an account when its email is verified. New guest orders also attach automatically when their email matches a verified account, even without login. Attaching sets the order's user; the guest email stays on the order; an order already owned by another account is never re-assigned.
7. **Account features, phase 1.** Order history and detail, profile (name, phone), address book, change email (re-verified), change password, "delete my account" request (anonymises personal fields, keeps orders for the legal retention period). No wishlist, reorder or saved payment details.
8. **Abuse protection.** Rate limits (tracking lookups, sign-up, login, password reset, order placement per IP) plus a honeypot field. No CAPTCHA in phase 1; revisit if fake orders appear.

Dependencies for later tickets: Oscar guest-checkout settings must be enabled (off by default); basket cookie settings need the spike's SSR-forwarding result; the signed order link and the tracking page need API endpoints (API boundary ticket); the account-deletion request is a KVKK data-subject path to confirm with the lawyer.
