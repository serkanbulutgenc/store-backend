"""Products with Orders are never hard-deleted; archive them instead."""

from __future__ import annotations

from django.db.models import ProtectedError
from django.db.models.signals import post_delete
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from oscar.apps.catalogue.models import Product
from oscar.apps.order.models import Line

from store_backend.catalog.models import ProductPhoto


def has_orders(product: Product) -> bool:
    """True if this Product, or any of its Variants, appears on an Order line."""
    ids = [product.pk, *product.children.values_list("pk", flat=True)]
    return Line.objects.filter(product_id__in=ids).exists()


@receiver(pre_delete, sender=Product)
def refuse_delete_with_orders(sender, instance: Product, **kwargs) -> None:
    if has_orders(instance):
        msg = "Siparişi olan ürün silinemez; arşivleyin."
        raise ProtectedError(msg, {instance})


@receiver(post_delete, sender=ProductPhoto)
def schedule_file_removal(sender, instance: ProductPhoto, **kwargs) -> None:
    """However an image goes (API delete or a cascade), its files leave disk later."""
    from store_backend.services import media  # noqa: PLC0415

    media.schedule_removal(instance)
