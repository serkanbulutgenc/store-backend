# Reviews are moderated, verified-buyer ratings

Only a Shopper with a delivered Order line for a Product may review it, and a line that was fully returned (a return that is approved, received or refunded) no longer counts. One review per Shopper and Product. A review is stars (1-5) plus optional text, with no title.

**Stars alone publish at once; any text waits for a moderator.** Owner and Staff approve, reject or delete from a Dashboard queue. This is a correctable action, so it needs no extra permission ([ADR 0001](0001-staff-trusted-with-correctable-actions.md)), and every decision is in the Audit log. Editing the text of a review sends it back to the queue; changing only the stars does not. Clearing the text publishes the stars.

**Why moderate.** Turkish consumer rules on commercial communication make the seller answerable for content it publishes, and the shop has no staff to react to complaints around the clock. Holding text until a person has read it is the cheapest way to keep abuse, personal data and other sellers' advertising off the product page. Stars carry no such risk, so they are not held, which keeps the average honest without making buyers wait.

**Text is basic markdown, stored raw.** Bold, italic, strikethrough, paragraphs, line breaks and bulleted lists. The server refuses anything that looks like HTML and strips markdown links and images, so no clickable link or external image can be published; bare URLs stay plain text. The Storefront renders the source with a small parser into React elements, never as HTML. Emoji are stored as Unicode; the form converts a small set of `:shortcode:` names on submit. At most 2000 characters.

**Shown as "Ayşe K."** (first name and last initial), never the email. A Shopper without a name shows as "Müşteri".

**Account deletion removes the Shopper's reviews** (as it does their Favorites), so no text written under a name outlives the account. The Product's average changes accordingly.

Rejected: Oscar's reviews app ([ADR 0003](0003-attributes-favorites-reviews-are-own-models.md)), publishing text first and moderating on report, and letting any signed-in Shopper review. Not built yet: a rating sort, helpful votes and a shop reply.
