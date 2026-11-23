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
from store_backend.services import banners
from store_backend.services import browse
from store_backend.services import collections

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
    shelf_price: Money | None
    campaign_percentage: Decimal | None
    sold_out: bool
    image: CardImageOut | None
    colours: list[SwatchOut]
    rating_average: float | None
    rating_count: int


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
    logo_url: str | None


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
    on_sale: bool = Field(default=False, description="Only Products with an old price.")
    sort: Literal[
        "newest",
        "price_asc",
        "price_desc",
        "name_asc",
        "rating_desc",
        "popular",
        "relevance",
    ] = "newest"
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
        on_sale=params.on_sale,
    )
    return browse.listing(filters, sort=params.sort, page=params.page)


class CategoryChildOut(Schema):
    name: str
    slug: str


class CategoryNodeOut(Schema):
    name: str
    slug: str
    child_count: int
    image_url: str | None
    children: list[CategoryNodeOut]


@router.get(
    "/categories",
    operation_id="catalog_list_categories",
    response=list[CategoryNodeOut],
)
def list_categories(request, depth: int = Query(2, ge=1, le=5)):
    """The category tree for the header menu and the homepage: `depth` levels
    (1 to 5, default 2), each node with its `child_count`
    and the `image_url` of its thumbnail (null until the image is ready)."""
    return browse.category_tree(depth)


class CategoryPageOut(Schema):
    name: str
    slug: str
    description: str
    seo_title: str
    seo_description: str
    image: CardImageOut | None
    breadcrumbs: list[CategoryChildOut]
    children: list[CategoryChildOut]


@router.get(
    "/categories/{slug}",
    operation_id="catalog_get_category",
    response=CategoryPageOut,
)
def get_category(request, slug: str):
    """A Category page's copy, image and SEO fields; hidden Categories are 404."""
    return browse.category_page(slug)


class BrandOut(Schema):
    id: int
    name: str
    logo_url: str | None


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
    shelf_price: Money | None
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
    has_size_guide: bool
    fit_note: str
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


class SizeGuideOut(Schema):
    text: str


@router.get(
    "/products/{slug}/size-guide",
    operation_id="catalog_get_product_size_guide",
    response=SizeGuideOut,
)
def get_product_size_guide(request, slug: str):
    """The Size guide that applies to a published Product, loaded when the dialog
    opens. 404 when none applies (no guide written, or a One-size Product)."""
    return {"text": browse.size_guide(slug)}


class CollectionPageOut(Schema):
    name: str
    slug: str
    description: str
    image: CardImageOut | None
    items: list[ProductCardOut]


@router.get(
    "/collections/{slug}",
    operation_id="catalog_get_collection",
    response=CollectionPageOut,
)
def get_collection(request, slug: str):
    """A published Collection's Products in the Owner's order. Draft and archived
    Products are left out, sold-out ones are shown as sold out. A Collection that is
    not published or has no visible Product is 404."""
    return collections.page(slug)


@router.get(
    "/products/{slug}/similar",
    operation_id="catalog_get_product_similar",
    response=list[ProductCardOut],
)
def get_product_similar(request, slug: str):
    """The ranked "Benzer ürünler" cards of a published Product."""
    return browse.similar(slug)


@router.get(
    "/recently-viewed",
    operation_id="catalog_get_recently_viewed",
    response=list[ProductCardOut],
)
def get_recently_viewed(request, slugs: str = ""):
    """Fresh cards for the Product slugs a browser kept ("Son baktıklarınız"), comma
    separated and in that order. Products that are gone or sold out are left out."""
    wanted = (part.strip() for part in slugs.split(","))
    return browse.recently_viewed([slug for slug in wanted if slug])


class AnnouncementOut(Schema):
    id: int
    message: str
    link: str


class PictureBannerOut(Schema):
    id: int
    alt: str
    link: str
    image: list[ImageSizeOut]
    mobile_image: list[ImageSizeOut] | None


class BannersOut(Schema):
    announcement: list[AnnouncementOut]
    home_hero: list[PictureBannerOut]
    home_mid: list[PictureBannerOut]
    category_top: list[PictureBannerOut]


@router.get(
    "/banners",
    operation_id="catalog_list_banners",
    response=BannersOut,
)
def list_banners(request, category: str | None = None):
    """The active Banners per slot, in order: published and inside their dates. The
    `category_top` slot is filled only when a Category slug is given, with the Banners
    tied to that Category and those tied to none; a hidden Category is 404."""
    return banners.public(category)
