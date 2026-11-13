# ruff: noqa: TC001, TC003
"""Storefront catalog endpoints: the product list (category pages and search) and the
product page. Public; draft and archived Products are not found."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from ninja import Field
from ninja import Query
from ninja import Router
from ninja import Schema

from store_backend.api.money import Money
from store_backend.services import browse

router = Router(tags=["catalog"], auth=None)


class ImageSizeOut(Schema):
    width: int
    url: str


class CardImageOut(Schema):
    alt: str
    sizes: list[ImageSizeOut]


class SwatchOut(Schema):
    id: int
    name: str
    hex_code: str


class ProductCardOut(Schema):
    id: int
    slug: str
    title: str
    price: Money
    old_price: Money | None
    sold_out: bool
    image: CardImageOut | None
    colours: list[SwatchOut]


class ColourFacet(Schema):
    id: int
    name: str
    hex_code: str
    count: int


class SizeFacet(Schema):
    size: str
    count: int


class BrandFacet(Schema):
    id: int
    name: str
    count: int


class PriceFacet(Schema):
    min: Money | None
    max: Money | None


class OptionFacet(Schema):
    id: int
    label: str
    count: int


class AttributeFacet(Schema):
    id: int
    name: str
    product_class: str
    options: list[OptionFacet]


class FacetsOut(Schema):
    colours: list[ColourFacet]
    sizes: list[SizeFacet]
    brands: list[BrandFacet]
    price: PriceFacet
    attributes: list[AttributeFacet]


class ProductPageOut(Schema):
    items: list[ProductCardOut]
    total: int
    page: int
    page_size: int
    facets: FacetsOut


class ProductListQuery(Schema):
    category: str | None = Field(None, description="Category slug, children included.")
    q: str = ""
    colour: list[int] = Field(default_factory=list)
    size: list[str] = Field(default_factory=list)
    brand: list[int] = Field(default_factory=list)
    option: list[int] = Field(
        default_factory=list,
        description="Attribute option ids: any of one attribute's, all attributes.",
    )
    price_min: Decimal | None = Field(None, ge=0)
    price_max: Decimal | None = Field(None, ge=0)
    in_stock: bool = True
    sort: Literal["newest", "price_asc", "price_desc", "relevance"] = "newest"
    page: int = Field(1, ge=1)


@router.get(
    "/products",
    operation_id="catalog_list_products",
    response=ProductPageOut,
)
def list_products(request, params: Query[ProductListQuery]):
    """Product cards, 24 per page, with facet counts that respect the other filters."""
    filters = browse.Filters(
        category=params.category,
        q=params.q,
        colours=params.colour,
        sizes=params.size,
        brands=params.brand,
        options=params.option,
        price_min=params.price_min,
        price_max=params.price_max,
        in_stock=params.in_stock,
    )
    return browse.listing(filters, sort=params.sort, page=params.page)


class CategoryChildOut(Schema):
    name: str
    slug: str


class CategoryNodeOut(Schema):
    name: str
    slug: str
    children: list[CategoryChildOut]


@router.get(
    "/categories",
    operation_id="catalog_list_categories",
    response=list[CategoryNodeOut],
)
def list_categories(request):
    """The two-level category tree for the header menu and the homepage."""
    return browse.category_tree()


class BrandOut(Schema):
    id: int
    name: str


class CrumbOut(Schema):
    name: str
    slug: str


class PagePhotoOut(Schema):
    id: int
    colour_id: int | None
    is_primary: bool
    alt: str
    sizes: list[ImageSizeOut]


class PageVariantOut(Schema):
    id: int
    sku: str
    colour: SwatchOut
    size: str
    price: Money
    available: int
    in_stock: bool
    remaining: int | None


class AttributeBlockOut(Schema):
    name: str
    values: list[str]


class ProductDetailOut(ProductCardOut):
    description: str
    brand: BrandOut | None
    breadcrumbs: list[CrumbOut]
    is_hygiene: bool
    attributes: list[AttributeBlockOut]
    variants: list[PageVariantOut]
    photos: list[PagePhotoOut]


@router.get(
    "/products/{slug}",
    operation_id="catalog_get_product",
    response=ProductDetailOut,
)
def get_product(request, slug: str):
    return browse.detail(slug)
