from celery import shared_task

from store_backend.services import outbox


@shared_task
def deliver_message(message_id: int) -> None:
    """One delivery attempt. A failed attempt reschedules itself in the database."""
    outbox.deliver(message_id)


@shared_task
def dispatch_due_messages() -> int:
    """Safety net: pick up pending messages whose time has come (after a worker or
    broker outage, and for every retry)."""
    return outbox.dispatch_due()
