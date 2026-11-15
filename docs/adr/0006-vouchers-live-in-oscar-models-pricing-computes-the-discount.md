# Vouchers live in Oscar's voucher models; our pricing code computes the discount

A Voucher is stored in Oscar's `Voucher` and `ConditionalOffer` (with its benefit and condition) models, and a Redemption is Oscar's `VoucherApplication`. This is the opposite of [ADR 0003](0003-attributes-favorites-reviews-are-own-models.md), where Oscar's schema did not fit. Here it does: code, dates, usage mode, percentage or fixed benefit and minimum basket condition are what a Voucher is.

Oscar's `Applicator` is not used. Checkout already prices every line from our own KDV snapshots, and the Voucher discount is computed in `services/pricing.py`: spread across item lines in proportion to their value, rounded to kuruş, any leftover kuruş on the largest line, with KDV split on each discounted line total. Order lines store the discounted amounts and the discount is also written to Oscar's order discount record. Oscar's own spreading cannot guarantee that stored line amounts, KDV snapshots and invoice totals add up.

Oscar gaps are filled in our code: Guests are matched by the Order's email for "once per Customer" (Oscar rejects Guests); the total redemption cap counts live `VoucherApplication` rows under a lock on the Voucher at placement; cancelling an Order, including the automatic cancel, deletes its application and decrements `num_orders`.

We rejected a fully own Voucher model: it would duplicate fields Oscar already holds and migrate. We rejected Oscar's full offer pipeline: it fights the per-line KDV snapshot.

Consequences: invoice and Order history depend on the pro-rata allocation, so changing it later means a data migration. Oscar upgrades must be checked against the voucher and offer tables we now use.
