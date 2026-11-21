from celery import shared_task

from store_backend.services import order_ops
from store_backend.services import stock
from store_backend.services import stock_alerts


@shared_task
def process_unpaid_transfers() -> dict[str, int]:
    """24 h reminder, then the 48 h auto-cancel that releases the stock."""
    return {
        "reminded": order_ops.send_payment_reminders(),
        "cancelled": order_ops.cancel_unpaid_transfers(),
    }


@shared_task
def send_low_stock_digest() -> int:
    return stock.send_low_stock_digest()


@shared_task
def purge_stock_alerts() -> dict[str, int]:
    """Remove lapsed Stock alerts and Alert consent records past 3 years."""
    return stock_alerts.purge()
