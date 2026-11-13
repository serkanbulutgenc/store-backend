# Legal pages are Owner-edited, immutable versions in the database, not flatpages

Legal page text moves from Markdown files in the frontend repo into the database, so the Owner can edit it in the Dashboard and Shoppers can search it. Every save creates a new, immutable Legal page version, and Consent records and Pre-information snapshots keep pointing at the version a Shopper accepted.

We rejected `django.contrib.flatpages` (installed only because Oscar requires it): a flatpage is overwritten on edit, so the exact accepted text would be lost. We also rejected leaving the text in the repo, which keeps the lawyer-to-deploy workflow the Owner wanted to remove.
