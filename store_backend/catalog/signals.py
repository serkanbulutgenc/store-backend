"""Products with Orders are never hard-deleted; archive them instead."""

from __future__ import annotations

from django.db.models import ProtectedError
from django.db.models.signals import post_delete
from django.db.models.signals import post_save
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from oscar.apps.catalogue.models import Category
from oscar.apps.catalogue.models import Product
from oscar.apps.catalogue.models import ProductCategory
from oscar.apps.order.models import Line

from store_backend.catalog.models import Brand
from store_backend.catalog.models import Colour
from store_backend.catalog.models import ProductPhoto
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import VariantProfile


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


# --- storefront search text -----------------------------------------------------
# The search text covers the title, brand, category names, colours and SKUs, so
# anything that changes one of them rebuilds the affected Products' text.


def _reindex(product: Product) -> None:
    from store_backend.services import search  # noqa: PLC0415

    if ProductProfile.objects.filter(product=product).exists():
        search.reindex(product)


@receiver(post_save, sender=Product)
def reindex_on_title(sender, instance: Product, **kwargs) -> None:
    if not kwargs.get("raw") and instance.structure == Product.PARENT:
        _reindex(instance)


@receiver(post_save, sender=ProductProfile)
def reindex_on_profile(sender, instance: ProductProfile, **kwargs) -> None:
    update_fields = kwargs.get("update_fields")
    # The reindex itself writes with `.update()`, so this never recurses.
    if not kwargs.get("raw") and (update_fields is None or "brand" in update_fields):
        _reindex(instance.product)


@receiver(post_save, sender=VariantProfile)
@receiver(post_delete, sender=VariantProfile)
def reindex_on_variant(sender, instance: VariantProfile, **kwargs) -> None:
    if not kwargs.get("raw") and Product.objects.filter(pk=instance.parent_id).exists():
        _reindex(instance.parent)


@receiver(post_save, sender=ProductCategory)
@receiver(post_delete, sender=ProductCategory)
def reindex_on_category_link(sender, instance, **kwargs) -> None:
    if (
        not kwargs.get("raw")
        and Product.objects.filter(pk=instance.product_id).exists()
    ):
        _reindex(instance.product)


@receiver(post_save, sender=Brand)
def reindex_on_brand(sender, instance: Brand, **kwargs) -> None:
    from store_backend.services import search  # noqa: PLC0415

    if not kwargs.get("raw"):
        search.reindex_where(brand=instance)


@receiver(post_save, sender=Colour)
def reindex_on_colour(sender, instance: Colour, **kwargs) -> None:
    from store_backend.services import search  # noqa: PLC0415

    if not kwargs.get("raw"):
        parents = VariantProfile.objects.filter(colour=instance).values("parent_id")
        search.reindex_where(product_id__in=parents)


@receiver(post_save, sender=Category)
def reindex_on_category(sender, instance: Category, **kwargs) -> None:
    from store_backend.services import search  # noqa: PLC0415

    if not kwargs.get("raw"):
        search.reindex_category(instance)


@receiver(post_save, sender=Category)
def create_category_profile(sender, instance: Category, created, **kwargs) -> None:
    """A new Category goes last among its siblings, so the menu keeps creation order
    until someone reorders it."""
    from django.db.models import Max  # noqa: PLC0415

    from store_backend.catalog.models import CategoryProfile  # noqa: PLC0415

    if not created or kwargs.get("raw"):
        return
    siblings = CategoryProfile.objects.filter(
        category__depth=instance.depth,
        category__path__startswith=instance.path[: -Category.steplen],
    ).exclude(category=instance)
    top = siblings.aggregate(top=Max("sort_order"))["top"] or 0
    CategoryProfile.objects.get_or_create(
        category=instance,
        defaults={"sort_order": top + 1},
    )
