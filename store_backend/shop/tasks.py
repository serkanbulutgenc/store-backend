from celery import shared_task

from store_backend.services import order_ops
from store_backend.services import stock


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
