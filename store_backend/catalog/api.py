# ruff: noqa: TC001, TC002, TC003
"""Dashboard catalog endpoints: Products and Variants, Categories, Brands, Suppliers,
the Colour list, stock adjustments and the low-stock list.

Cost price, Supplier and supplier code are Owner-only: for anyone else they are left
out of responses (not nulled) and rejected on input.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db.models import F
from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja import Field
from ninja import File
from ninja import Form
from ninja import Router
from ninja import Schema
from ninja.files import UploadedFile
from ninja.pagination import paginate
from oscar.apps.catalogue.models import Category
from oscar.apps.catalogue.models import Product
from oscar.apps.catalogue.models import ProductClass

from store_backend.api.errors import UnprocessableError
from store_backend.api.money import Money
from store_backend.api.ordering import apply_ordering
from store_backend.api.pagination import PageOffsetPagination
from store_backend.api.permissions import requires
from store_backend.catalog.models import Brand
from store_backend.catalog.models import Colour
from store_backend.catalog.models import ProductPhoto
from store_backend.catalog.models import ProductStatus
from store_backend.catalog.models import Supplier
from store_backend.services import catalog
from store_backend.services import catalog_policy
from store_backend.services import catalog_views as views
from store_backend.services import media
from store_backend.services import stock
from store_backend.staff.permissions import Perm

router = Router(tags=["catalog"])

PRODUCT_ORDERING = {"id", "title", "shelf_price", "status"}


# --- schemas ---------------------------------------------------------------


class NamedOut(Schema):
    id: int
    name: str


class ColourOut(Schema):
    id: int
    name: str
    code: str
    hex_code: str
    is_active: bool


class ColourIn(Schema):
    name: str
    hex_code: str


class ColourPatch(Schema):
    name: str | None = None
    hex_code: str | None = None
    is_active: bool | None = None


class NameIn(Schema):
    name: str


class NamePatch(Schema):
    name: str | None = None


class SupplierOut(Schema):
    id: int
    name: str
    code: str


class SupplierIn(Schema):
    name: str
    code: str


class SupplierPatch(Schema):
    name: str | None = None
    code: str | None = None


class CategoryOut(Schema):
    id: int
    name: str
    slug: str
    parent_id: int | None


class CategoryIn(Schema):
    name: str
    parent_id: int | None = None


class ProductClassOut(Schema):
    id: int
    name: str
    size_scheme: str
    sizes: list[str]
    default_kdv_rate: Money
    is_hygiene: bool


class ProductIn(Schema):
    title: str
    product_class_id: int
    category_id: int
    shelf_price: Money
    old_price: Money | None = None
    brand_id: int | None = None
    kdv_rate: Decimal | None = None
    low_stock_threshold: int | None = None
    slug: str | None = None
    # Owner only:
    supplier_id: int | None = None
    supplier_code: str = ""
    cost_price: Money | None = None


class ProductPatch(Schema):
    title: str | None = None
    category_id: int | None = None
    brand_id: int | None = None
    shelf_price: Money | None = None
    old_price: Money | None = None
    kdv_rate: Decimal | None = None
    low_stock_threshold: int | None = None
    slug: str | None = None
    # Owner only:
    supplier_id: int | None = None
    supplier_code: str | None = None
    cost_price: Money | None = None


class StatusIn(Schema):
    status: ProductStatus


class VariantIn(Schema):
    colour_id: int
    size: str
    sku: str | None = None
    barcode: str | None = None
    stock: int = Field(0, ge=0)
    price_override: Money | None = None


class VariantPatch(Schema):
    sku: str | None = None
    barcode: str | None = None
    price_override: Money | None = None


class StockAdjustmentIn(Schema):
    delta: int = Field(..., ge=-100000, le=100000)
    reason: str = Field(..., max_length=200)


class StockOut(Schema):
    on_hand: int
    allocated: int


class VariantOut(Schema):
    id: int
    product_id: int
    sku: str
    barcode: str | None
    colour: ColourOut
    size: str
    price_override: Money | None
    stock: StockOut
    is_low_stock: bool


class ProductListOut(Schema):
    id: int
    title: str
    slug: str
    status: ProductStatus
    category: NamedOut | None
    shelf_price: Money
    variant_count: int
    stock_total: int


class ProductOut(Schema):
    id: int
    title: str
    slug: str
    status: ProductStatus
    product_class: ProductClassOut
    category: NamedOut | None
    brand: NamedOut | None
    shelf_price: Money
    old_price: Money | None
    kdv_rate: Money
    low_stock_threshold: int
    variants: list[VariantOut]
    # Owner only; left out of the response for everyone else.
    cost_price: Money | None = None
    supplier: SupplierOut | None = None
    supplier_code: str | None = None


class LowStockOut(Schema):
    variant_id: int
    product_id: int
    product_title: str
    sku: str
    colour: str
    size: str
    on_hand: int
    threshold: int


# --- reference data --------------------------------------------------------


@router.get(
    "/product-classes",
    auth=requires(Perm.VIEW_PRODUCTS),
    operation_id="catalog_list_product_classes",
    response=list[ProductClassOut],
)
def list_product_classes(request):
    classes = ProductClass.objects.select_related("profile").order_by("pk")
    return [views.product_class_dict(pc) for pc in classes]


@router.get(
    "/colours",
    auth=requires(Perm.VIEW_PRODUCTS),
    operation_id="catalog_list_colours",
    response=list[ColourOut],
)
def list_colours(request):
    return Colour.objects.all()


@router.post(
    "/colours",
    auth=requires(Perm.MANAGE_COLOURS),
    operation_id="catalog_create_colour",
    response={201: ColourOut},
)
def create_colour(request, data: ColourIn):
    return 201, catalog.create_colour(data.name, data.hex_code)


@router.patch(
    "/colours/{colour_id}",
    auth=requires(Perm.MANAGE_COLOURS),
    operation_id="catalog_update_colour",
    response=ColourOut,
)
def update_colour(request, colour_id: int, data: ColourPatch):
    colour = get_object_or_404(Colour, pk=colour_id)
    return catalog.update_colour(colour, **data.model_dump(exclude_unset=True))


@router.get(
    "/brands",
    auth=requires(Perm.VIEW_PRODUCTS),
    operation_id="catalog_list_brands",
    response=list[NamedOut],
)
def list_brands(request):
    return Brand.objects.all()


@router.post(
    "/brands",
    auth=requires(Perm.MANAGE_BRANDS),
    operation_id="catalog_create_brand",
    response={201: NamedOut},
)
def create_brand(request, data: NameIn):
    return 201, catalog.create_brand(data.name)


@router.patch(
    "/brands/{brand_id}",
    auth=requires(Perm.MANAGE_BRANDS),
    operation_id="catalog_update_brand",
    response=NamedOut,
)
def update_brand(request, brand_id: int, data: NamePatch):
    return catalog.update_brand(get_object_or_404(Brand, pk=brand_id), name=data.name)


@router.get(
    "/suppliers",
    auth=requires(Perm.MANAGE_SUPPLIERS),
    operation_id="catalog_list_suppliers",
    response=list[SupplierOut],
)
def list_suppliers(request):
    return Supplier.objects.all()


@router.post(
    "/suppliers",
    auth=requires(Perm.MANAGE_SUPPLIERS),
    operation_id="catalog_create_supplier",
    response={201: SupplierOut},
)
def create_supplier(request, data: SupplierIn):
    return 201, catalog.create_supplier(data.name, data.code)


@router.patch(
    "/suppliers/{supplier_id}",
    auth=requires(Perm.MANAGE_SUPPLIERS),
    operation_id="catalog_update_supplier",
    response=SupplierOut,
)
def update_supplier(request, supplier_id: int, data: SupplierPatch):
    supplier = get_object_or_404(Supplier, pk=supplier_id)
    return catalog.update_supplier(supplier, **data.model_dump(exclude_unset=True))


@router.get(
    "/categories",
    auth=requires(Perm.VIEW_PRODUCTS),
    operation_id="catalog_list_categories",
    response=list[CategoryOut],
)
def list_categories(request):
    return [views.category_dict(c) for c in Category.objects.all().order_by("path")]


@router.post(
    "/categories",
    auth=requires(Perm.MANAGE_CATEGORIES),
    operation_id="catalog_create_category",
    response={201: CategoryOut},
)
def create_category(request, data: CategoryIn):
    parent = catalog.ref(Category, data.parent_id, "parent_id")
    return 201, views.category_dict(catalog.create_category(data.name, parent=parent))


@router.patch(
    "/categories/{category_id}",
    auth=requires(Perm.MANAGE_CATEGORIES),
    operation_id="catalog_update_category",
    response=CategoryOut,
)
def update_category(request, category_id: int, data: NameIn):
    c = catalog.update_category(
        get_object_or_404(Category, pk=category_id),
        name=data.name,
    )
    return views.category_dict(c)


# --- products and variants -----------------------------------------------------


@router.get(
    "/products",
    auth=requires(Perm.VIEW_PRODUCTS),
    operation_id="catalog_list_products_admin",
    response=list[ProductListOut],
)
@paginate(PageOffsetPagination)
def list_products(
    request,
    q: str | None = None,
    status: str | None = None,
    category_id: int | None = None,
    ordering: str | None = None,
):
    """Search by title, slug, or an exact SKU or barcode of any Variant."""
    qs = (
        Product.objects.filter(structure=Product.PARENT)
        .select_related("profile")
        .prefetch_related("categories", "children__stockrecords")
        .annotate(shelf_price=F("profile__shelf_price"), status=F("profile__status"))
    )
    if q:
        term = q.strip()
        qs = qs.filter(
            Q(title__icontains=term)
            | Q(profile__slug__icontains=term)
            | Q(children__variant_profile__sku__iexact=term)
            | Q(children__variant_profile__barcode=term),
        ).distinct()
    if status:
        if status not in ProductStatus.values:
            msg = "Geçersiz durum."
            raise UnprocessableError(msg, fields={"status": [msg]})
        qs = qs.filter(profile__status=status)
    if category_id:
        qs = qs.filter(categories__pk=category_id)
    qs = apply_ordering(qs, ordering or "-id", allowed=PRODUCT_ORDERING)
    return MappedRows(qs, views.list_dict)


class MappedRows:
    """Queryset-like wrapper: pagination counts and slices the queryset, then each
    row of the slice is mapped to its response dict."""

    def __init__(self, qs, fn) -> None:
        self.qs = qs
        self.fn = fn

    def all(self) -> MappedRows:
        return self

    def count(self) -> int:
        return self.qs.count()

    def __getitem__(self, item):
        return [self.fn(row) for row in self.qs[item]]


@router.get(
    "/products/{product_id}",
    auth=requires(Perm.VIEW_PRODUCTS),
    operation_id="catalog_get_product",
    response=ProductOut,
    exclude_unset=True,
)
def get_product(request, product_id: int):
    return views.product_dict(request.auth, catalog.get_product(product_id))


@router.post(
    "/products",
    auth=requires(Perm.MANAGE_PRODUCTS),
    operation_id="catalog_create_product",
    response={201: ProductOut},
    exclude_unset=True,
)
def create_product(request, data: ProductIn):
    user = request.auth
    catalog_policy.require_owner_fields_allowed(
        user,
        data.model_dump(exclude_unset=True),
    )
    ref = catalog.ref
    product = catalog.create_product(
        title=data.title,
        product_class=ref(ProductClass, data.product_class_id, "product_class_id"),
        category=ref(Category, data.category_id, "category_id"),
        shelf_price=data.shelf_price,
        old_price=data.old_price,
        brand=ref(Brand, data.brand_id, "brand_id"),
        supplier=ref(Supplier, data.supplier_id, "supplier_id"),
        supplier_code=data.supplier_code,
        cost_price=data.cost_price,
        kdv_rate=data.kdv_rate,
        low_stock_threshold=data.low_stock_threshold,
        slug=data.slug,
    )
    return 201, views.product_dict(user, catalog.get_product(product.pk))


@router.patch(
    "/products/{product_id}",
    auth=requires(Perm.MANAGE_PRODUCTS),
    operation_id="catalog_update_product",
    response=ProductOut,
    exclude_unset=True,
)
def update_product(request, product_id: int, data: ProductPatch):
    user = request.auth
    product = catalog.patch_product(
        user,
        product_id,
        data.model_dump(exclude_unset=True),
    )
    return views.product_dict(user, product)


@router.post(
    "/products/{product_id}/status",
    auth=requires(Perm.MANAGE_PRODUCTS),
    operation_id="catalog_set_product_status",
    response=ProductOut,
    exclude_unset=True,
)
def set_product_status(request, product_id: int, data: StatusIn):
    """Publish or return to draft; archiving and unarchiving are Owner-only."""
    user = request.auth
    product = catalog.change_status(user, product_id, data.status)
    return views.product_dict(user, product)


@router.post(
    "/products/{product_id}/variants",
    auth=requires(Perm.MANAGE_PRODUCTS),
    operation_id="catalog_create_variant",
    response={201: VariantOut},
)
def create_variant(request, product_id: int, data: VariantIn):
    colour = catalog.ref(Colour, data.colour_id, "colour_id")
    variant = catalog.create_variant(
        catalog.get_product(product_id),
        colour=colour,
        size=data.size,
        sku=data.sku,
        barcode=data.barcode,
        stock=data.stock,
        price_override=data.price_override,
    )
    return 201, views.variant_dict(catalog.get_variant(variant.pk))


@router.patch(
    "/variants/{variant_id}",
    auth=requires(Perm.MANAGE_PRODUCTS),
    operation_id="catalog_update_variant",
    response=VariantOut,
)
def update_variant(request, variant_id: int, data: VariantPatch):
    catalog.update_variant(
        request.auth,
        catalog.get_variant(variant_id),
        **data.model_dump(exclude_unset=True),
    )
    return views.variant_dict(catalog.get_variant(variant_id))


# --- stock ---------------------------------------------------------------------


@router.post(
    "/variants/{variant_id}/stock-adjustments",
    auth=requires(Perm.ADJUST_STOCK),
    operation_id="catalog_adjust_stock",
    response=VariantOut,
)
def adjust_stock(request, variant_id: int, data: StockAdjustmentIn):
    variant = catalog.get_variant(variant_id)
    stock.adjust(request.auth, variant, delta=data.delta, reason=data.reason)
    return views.variant_dict(catalog.get_variant(variant_id))


@router.get(
    "/stock/low",
    auth=requires(Perm.VIEW_PRODUCTS),
    operation_id="catalog_list_low_stock",
    response=list[LowStockOut],
)
@paginate(PageOffsetPagination)
def list_low_stock(request):
    return MappedRows(stock.low_stock_variants(), views.low_stock_dict)


# --- product photos -------------------------------------------------------------


class ImageDerivativeOut(Schema):
    width: int
    url: str


class ProductImageOut(Schema):
    id: int
    product_id: int
    colour: ColourOut | None
    sort_order: int
    is_primary: bool
    status: str
    alt_text: str  # what is shown: the custom text, else product name and colour
    custom_alt_text: str  # empty when the default applies
    original_url: str
    width: int
    height: int
    derivatives: list[ImageDerivativeOut]
    error: str


class ProductImagePatch(Schema):
    colour_id: int | None = None
    alt_text: str | None = None
    is_primary: bool | None = None


class ImageOrderIn(Schema):
    ids: list[int]


def _images(product: Product) -> list[ProductPhoto]:
    return list(
        ProductPhoto.objects.filter(product=product).select_related(
            "colour",
            "product",
        ),
    )


def _image(pk: int) -> ProductPhoto:
    return get_object_or_404(
        ProductPhoto.objects.select_related("colour", "product"),
        pk=pk,
    )


@router.get(
    "/products/{product_id}/images",
    auth=requires(Perm.VIEW_PRODUCTS),
    operation_id="catalog_list_product_images",
    response=list[ProductImageOut],
)
def list_product_images(request, product_id: int):
    return [views.image_dict(i) for i in _images(catalog.get_product(product_id))]


@router.post(
    "/products/{product_id}/images",
    auth=requires(Perm.MANAGE_PRODUCT_PHOTOS),
    operation_id="catalog_upload_product_image",
    response={201: ProductImageOut},
)
def upload_product_image(
    request,
    product_id: int,
    file: File[UploadedFile],
    colour_id: Form[int | None] = None,
):
    """One photo per request (JPEG, PNG or WebP); processing continues in the
    background and the image reports `processing` until its derivatives exist.
    """
    product = catalog.get_product(product_id)
    colour = catalog.ref(Colour, colour_id, "colour_id")
    image = media.upload(request.auth, product, file, colour=colour)
    return 201, views.image_dict(_image(image.pk))


@router.patch(
    "/images/{image_id}",
    auth=requires(Perm.MANAGE_PRODUCT_PHOTOS),
    operation_id="catalog_update_product_image",
    response=ProductImageOut,
)
def update_product_image(request, image_id: int, data: ProductImagePatch):
    image = _image(image_id)
    sent = data.model_dump(exclude_unset=True)
    changes: dict[str, Any] = {}
    if "colour_id" in sent:
        changes["colour"] = catalog.ref(Colour, sent["colour_id"], "colour_id")
    if sent.get("alt_text") is not None:
        changes["alt_text"] = sent["alt_text"]
    if sent.get("is_primary") is not None:
        changes["is_primary"] = sent["is_primary"]
    media.update(request.auth, image, **changes)
    return views.image_dict(_image(image_id))


@router.delete(
    "/images/{image_id}",
    auth=requires(Perm.MANAGE_PRODUCT_PHOTOS),
    operation_id="catalog_delete_product_image",
    response={204: None},
)
def delete_product_image(request, image_id: int):
    media.delete(request.auth, _image(image_id))
    return 204, None


@router.post(
    "/products/{product_id}/images/order",
    auth=requires(Perm.MANAGE_PRODUCT_PHOTOS),
    operation_id="catalog_reorder_product_images",
    response=list[ProductImageOut],
)
def reorder_product_images(request, product_id: int, data: ImageOrderIn):
    product = catalog.get_product(product_id)
    media.reorder(request.auth, product, data.ids)
    return [views.image_dict(i) for i in _images(product)]
