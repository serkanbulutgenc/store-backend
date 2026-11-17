# Staff edits to published Collections are pending changes

Only the Owner publishes, unpublishes or deletes a Collection. Staff create and edit drafts and submit them for approval; the Owner approves (which publishes) or rejects (which returns the Collection to draft, with an optional reason shown to the submitter).

**A Staff edit to a published Collection is held as a pending change.** The live version stays on the Storefront until the Owner approves the change. A Collection has at most one pending change, and a second Staff edit amends it. While a pending change exists the Owner cannot edit the live Collection directly; they approve or reject it first, so neither side's work is silently lost. The Owner may edit a pending Collection or pending change before approving it.

**Why not the Review pattern.** An edited Review text goes back to the moderation queue and is hidden meanwhile ([ADR 0004](0004-reviews-are-moderated-verified-buyer-ratings.md)). That is cheap for a single comment, but taking a whole Collection off the Storefront over a typo or one added Product would punish the Shopper and the shop for a review step. Letting Staff edit live Collections with no review would make the approval gate pointless, since the product list is the content.

**Why this is a hard-to-reverse choice.** It needs a second stored version of a Collection and a Dashboard that shows what changed. Dropping it later, in favour of "Staff cannot edit a published Collection", is easy; adding it after data and screens assume one version is not.

Rejected: Staff editing live Collections directly, sending the whole Collection back to pending approval on any edit, and keeping a stale pending change when the Owner edits the live version. Not built yet: scheduled publishing, rule-based Collections, email to the Owner on submission, and Collections as a Home page section.
