from celery import shared_task

from store_backend.services import media


@shared_task
def process_product_image(image_id: int) -> None:
    """Make the WebP derivatives of one uploaded image."""
    media.process(image_id)


@shared_task
def process_category_image(category_id: int) -> None:
    """Make the WebP derivatives of a Category's uploaded image."""
    from store_backend.services import categories  # noqa: PLC0415

    categories.process_image(category_id)


@shared_task
def purge_removed_files() -> int:
    """Delete files whose grace period after an image delete has passed."""
    return media.purge_removed_files()
