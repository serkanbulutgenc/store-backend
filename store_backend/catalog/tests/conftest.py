"""Reuse the staff API test client fixtures."""

from store_backend.staff.tests.conftest import api_for  # noqa: F401


def add_ready_photo(product) -> None:
    """A processed photo record, so a Product can be published in a test."""
    from store_backend.catalog.models import ProductPhoto  # noqa: PLC0415

    ProductPhoto.objects.create(
        product=product,
        original="products/test.jpg",
        width=1000,
        height=1000,
        status=ProductPhoto.Status.READY,
        is_primary=True,
        sort_order=1,
    )
