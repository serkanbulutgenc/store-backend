# ruff: noqa: PLR2004
from __future__ import annotations

from http import HTTPStatus

import pytest
from oscar.apps.catalogue.models import ProductClass

from store_backend.catalog.models import AttributeValue
from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.catalog.sizes import sizes_for
from store_backend.services import attributes
from store_backend.services import catalog
from store_backend.staff.models import AuditEntry
from store_backend.staff.tests.conftest import staff_member

pytestmark = pytest.mark.django_db

ADMIN = "/api/admin"
STORE = "/api/store/products"


@pytest.fixture
def owner_api(api_for):
    return api_for(staff_member("Owner"), enrol=True)


@pytest.fixture
def staff_api(api_for):
    return api_for(staff_member("Staff"))


def giyim() -> ProductClass:
    return ProductClass.objects.get(name="Giyim")


def define(owner_api, name="Kumaş", kind="option", **extra) -> dict:
    body = {"name": name, "kind": kind, **extra}
    response = owner_api.post(f"{ADMIN}/product-classes/{giyim().pk}/attributes", body)
    assert response.status_code == HTTPStatus.CREATED, response.content
    return response.json()


def make_product(title="Elbise", klass=None, *, published=True):
    category = catalog.create_category(f"Kat {title}")
    product = catalog.create_product(
        title=title,
        product_class=klass or giyim(),
        category=category,
        shelf_price="100.00",
    )
    catalog.create_variant(
        product,
        colour=catalog.create_colour(f"Renk {title}", "#112233"),
        size=sizes_for(product.product_class.profile.size_scheme)[0],
        stock=5,
    )
    if published:
        ProductProfile.objects.filter(product=product).update(
            status=ProductStatus.PUBLISHED,
        )
    return product


def option_ids(attribute: dict) -> dict[str, int]:
    return {o["label"]: o["id"] for o in attribute["options"]}


# --- definitions -------------------------------------------------------------


def test_owner_defines_an_option_attribute_with_options(owner_api):
    attribute = define(
        owner_api,
        options=["Pamuk", "Viskon"],
        is_multiple=False,
        is_filterable=True,
    )
    assert attribute["kind"] == "option"
    assert [o["label"] for o in attribute["options"]] == ["Pamuk", "Viskon"]
    assert attribute["is_filterable"] is True
    assert attribute["product_count"] == 0
    listed = owner_api.get(f"{ADMIN}/product-classes/{giyim().pk}/attributes").json()
    assert [a["name"] for a in listed] == ["Kumaş"]
    assert (
        AuditEntry.objects.filter(summary__contains="Özellik oluşturuldu").count() == 1
    )


@pytest.mark.parametrize(
    "extra",
    [{"is_multiple": True}, {"is_filterable": True}, {"options": ["A"]}],
)
def test_a_text_attribute_is_plain(owner_api, extra):
    response = owner_api.post(
        f"{ADMIN}/product-classes/{giyim().pk}/attributes",
        {"name": "Bakım", "kind": "text", **extra},
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_invalid_definitions_are_refused(owner_api):
    url = f"{ADMIN}/product-classes/{giyim().pk}/attributes"
    for body in (
        {"name": " ", "kind": "text"},
        {"name": "X", "kind": "number"},
        {"name": "X", "kind": "option", "options": ["A", "a"]},
    ):
        assert owner_api.post(url, body).status_code == 422
    define(owner_api, name="Kumaş")
    clash = owner_api.post(url, {"name": "kumaş", "kind": "text"})
    assert clash.status_code == HTTPStatus.CONFLICT


def test_same_name_is_fine_in_another_class(owner_api):
    define(owner_api, name="Kumaş")
    other = ProductClass.objects.get(name="Aksesuar")
    response = owner_api.post(
        f"{ADMIN}/product-classes/{other.pk}/attributes",
        {"name": "Kumaş", "kind": "text"},
    )
    assert response.status_code == HTTPStatus.CREATED


def test_kind_and_multiplicity_cannot_change_but_the_name_can(owner_api):
    attribute = define(owner_api, options=["A"])
    url = f"{ADMIN}/attributes/{attribute['id']}"
    # Fields the PATCH schema does not know are ignored, never applied.
    ignored = owner_api.patch(url, {"kind": "text", "is_multiple": True}).json()
    assert (ignored["kind"], ignored["is_multiple"]) == ("option", False)
    renamed = owner_api.patch(url, {"name": "Kumaş türü", "is_filterable": True})
    assert renamed.json()["name"] == "Kumaş türü"
    assert renamed.json()["is_filterable"] is True


def test_text_attribute_cannot_be_made_filterable(owner_api):
    attribute = define(owner_api, name="Bakım", kind="text")
    response = owner_api.patch(
        f"{ADMIN}/attributes/{attribute['id']}",
        {"is_filterable": True},
    )
    assert response.status_code == 422


def test_options_can_be_added_renamed_and_removed(owner_api):
    attribute = define(owner_api, options=["Pamuk"])
    base = f"{ADMIN}/attributes/{attribute['id']}/options"
    added = owner_api.post(base, {"label": "Keten"})
    assert added.status_code == HTTPStatus.CREATED
    assert owner_api.post(base, {"label": "keten"}).status_code == 409
    renamed = owner_api.patch(
        f"{ADMIN}/attribute-options/{added.json()['id']}",
        {"label": "Saf keten"},
    )
    assert renamed.json()["label"] == "Saf keten"
    gone = owner_api.delete(f"{ADMIN}/attribute-options/{added.json()['id']}")
    assert gone.status_code == HTTPStatus.NO_CONTENT


def test_a_text_attribute_takes_no_options(owner_api):
    attribute = define(owner_api, name="Bakım", kind="text")
    response = owner_api.post(
        f"{ADMIN}/attributes/{attribute['id']}/options",
        {"label": "X"},
    )
    assert response.status_code == 422


# --- values -------------------------------------------------------------------


def put_values(api, product, values):
    return api.put(f"{ADMIN}/products/{product.pk}/attributes", {"values": values})


def test_staff_fill_in_values_but_cannot_define_attributes(owner_api, staff_api):
    kumas = define(owner_api, options=["Pamuk", "Viskon"])
    bakim = define(owner_api, name="Bakım", kind="text")
    product = make_product()
    response = put_values(
        staff_api,
        product,
        [
            {"attribute_id": kumas["id"], "option_ids": [option_ids(kumas)["Pamuk"]]},
            {"attribute_id": bakim["id"], "text": "30 derecede yıkayın"},
        ],
    )
    assert response.status_code == HTTPStatus.OK, response.content
    assert {v["attribute_id"] for v in response.json()} == {kumas["id"], bakim["id"]}
    detail = staff_api.get(f"{ADMIN}/products/{product.pk}").json()
    assert len(detail["attribute_values"]) == 2
    forbidden = staff_api.post(
        f"{ADMIN}/product-classes/{giyim().pk}/attributes",
        {"name": "X", "kind": "text"},
    )
    assert forbidden.status_code == HTTPStatus.FORBIDDEN


def test_a_single_value_attribute_takes_one_option_a_multi_takes_several(owner_api):
    single = define(owner_api, name="Kumaş", options=["A", "B"])
    multi = define(owner_api, name="Sezon", options=["Yaz", "Kış"], is_multiple=True)
    product = make_product()
    ids = option_ids(single)
    refused = put_values(
        owner_api,
        product,
        [{"attribute_id": single["id"], "option_ids": [ids["A"], ids["B"]]}],
    )
    assert refused.status_code == 422
    allowed = put_values(
        owner_api,
        product,
        [{"attribute_id": multi["id"], "option_ids": list(option_ids(multi).values())}],
    )
    assert allowed.status_code == HTTPStatus.OK
    assert AttributeValue.objects.filter(product=product).count() == 2


def test_values_must_match_kind_class_and_options(owner_api):
    kumas = define(owner_api, options=["A"])
    bakim = define(owner_api, name="Bakım", kind="text")
    product = make_product()
    other_class = make_product("Çanta", ProductClass.objects.get(name="Aksesuar"))
    foreign = option_ids(kumas)["A"]
    for values in (
        [{"attribute_id": bakim["id"], "option_ids": [foreign]}],
        [{"attribute_id": kumas["id"], "text": "serbest"}],
        [{"attribute_id": kumas["id"], "option_ids": [999999]}],
        [{"attribute_id": bakim["id"], "text": "x" * 501}],
        [{"attribute_id": kumas["id"]}, {"attribute_id": kumas["id"]}],
    ):
        assert put_values(owner_api, product, values).status_code == 422
    other = put_values(owner_api, other_class, [{"attribute_id": kumas["id"]}])
    assert other.status_code == 422


def test_empty_value_clears_and_untouched_attributes_stay(owner_api):
    kumas = define(owner_api, options=["A"])
    bakim = define(owner_api, name="Bakım", kind="text")
    product = make_product()
    put_values(
        owner_api,
        product,
        [
            {"attribute_id": kumas["id"], "option_ids": [option_ids(kumas)["A"]]},
            {"attribute_id": bakim["id"], "text": "Elde yıkayın"},
        ],
    )
    cleared = put_values(
        owner_api,
        product,
        [{"attribute_id": bakim["id"], "text": " "}],
    )
    assert [v["attribute_id"] for v in cleared.json()] == [kumas["id"]]


# --- deleting ---------------------------------------------------------------------


def test_deleting_an_attribute_in_use_needs_confirmation(owner_api):
    kumas = define(owner_api, options=["Pamuk"])
    product = make_product()
    put_values(
        owner_api,
        product,
        [{"attribute_id": kumas["id"], "option_ids": [option_ids(kumas)["Pamuk"]]}],
    )
    url = f"{ADMIN}/attributes/{kumas['id']}"
    refused = owner_api.delete(url)
    assert refused.status_code == HTTPStatus.CONFLICT
    assert refused.json()["code"] == "confirmation_required"
    assert "1 üründe" in refused.json()["message"]
    assert AttributeValue.objects.filter(product=product).exists()
    assert owner_api.delete(f"{url}?confirm=true").status_code == HTTPStatus.NO_CONTENT
    assert not AttributeValue.objects.filter(product=product).exists()


def test_an_unused_attribute_deletes_without_confirmation(owner_api):
    kumas = define(owner_api, options=["Pamuk"])
    assert owner_api.delete(f"{ADMIN}/attributes/{kumas['id']}").status_code == 204


def test_deleting_an_option_in_use_needs_confirmation_and_removes_its_values(owner_api):
    kumas = define(owner_api, options=["Pamuk", "Viskon"])
    product = make_product()
    pamuk = option_ids(kumas)["Pamuk"]
    put_values(
        owner_api,
        product,
        [{"attribute_id": kumas["id"], "option_ids": [pamuk]}],
    )
    url = f"{ADMIN}/attribute-options/{pamuk}"
    assert owner_api.delete(url).status_code == HTTPStatus.CONFLICT
    assert owner_api.delete(f"{url}?confirm=true").status_code == HTTPStatus.NO_CONTENT
    assert not AttributeValue.objects.filter(product=product).exists()


def test_deleting_a_class_removes_its_unused_attributes(owner_api):
    created = owner_api.post(
        f"{ADMIN}/product-classes",
        {"name": "Çanta", "size_scheme": "one_size", "default_kdv_rate": "20"},
    ).json()
    owner_api.post(
        f"{ADMIN}/product-classes/{created['id']}/attributes",
        {"name": "Malzeme", "kind": "text"},
    )
    assert (
        owner_api.delete(f"{ADMIN}/product-classes/{created['id']}").status_code == 204
    )


# --- search ------------------------------------------------------------------------


def search(client, q):
    return client.get(STORE, {"q": q}).json()["items"]


def test_attribute_values_are_searchable_and_follow_changes(owner_api, client):
    kumas = define(owner_api, options=["Pamuk", "Viskon"])
    bakim = define(owner_api, name="Bakım", kind="text")
    product = make_product("Elbise")
    pamuk = option_ids(kumas)["Pamuk"]
    put_values(
        owner_api,
        product,
        [
            {"attribute_id": kumas["id"], "option_ids": [pamuk]},
            {"attribute_id": bakim["id"], "text": "makinede yıkanabilir"},
        ],
    )
    assert [i["id"] for i in search(client, "pamuk")] == [product.pk]
    assert [i["id"] for i in search(client, "yıkanabilir")] == [product.pk]
    owner_api.patch(f"{ADMIN}/attribute-options/{pamuk}", {"label": "Organik"})
    assert search(client, "pamuk") == []
    assert [i["id"] for i in search(client, "organik")] == [product.pk]
    owner_api.delete(f"{ADMIN}/attribute-options/{pamuk}?confirm=true")
    assert search(client, "organik") == []


# --- storefront ----------------------------------------------------------------------


def test_product_page_shows_the_attribute_block_without_empty_attributes(
    owner_api,
    client,
):
    kumas = define(owner_api, options=["Pamuk"])
    mevsim = define(owner_api, name="Sezon", options=["Yaz", "Kış"], is_multiple=True)
    define(owner_api, name="Bakım", kind="text")  # no value: not shown
    product = make_product()
    put_values(
        owner_api,
        product,
        [
            {"attribute_id": kumas["id"], "option_ids": [option_ids(kumas)["Pamuk"]]},
            {
                "attribute_id": mevsim["id"],
                "option_ids": list(option_ids(mevsim).values()),
            },
        ],
    )
    slug = ProductProfile.objects.get(product=product).slug
    page = client.get(f"{STORE}/{slug}").json()
    assert page["attributes"] == [
        {"name": "Kumaş", "values": ["Pamuk"]},
        {"name": "Sezon", "values": ["Yaz", "Kış"]},
    ]


def test_a_product_without_values_has_an_empty_block(client):
    product = make_product()
    slug = ProductProfile.objects.get(product=product).slug
    assert client.get(f"{STORE}/{slug}").json()["attributes"] == []


class Fixture:
    def __init__(self, owner_api):
        self.kumas = define(owner_api, options=["Pamuk", "Viskon"], is_filterable=True)
        self.sezon = define(
            owner_api,
            name="Sezon",
            options=["Yaz", "Kış"],
            is_multiple=True,
            is_filterable=True,
        )
        self.gizli = define(owner_api, name="Not", options=["X"])  # not filterable
        self.k = option_ids(self.kumas)
        self.s = option_ids(self.sezon)
        self.a = make_product("A")  # pamuk, yaz
        self.b = make_product("B")  # pamuk, kış
        self.c = make_product("C")  # viskon, yaz+kış
        self.d = make_product("D")  # no values
        for product, kumas, sezon in (
            (self.a, "Pamuk", ["Yaz"]),
            (self.b, "Pamuk", ["Kış"]),
            (self.c, "Viskon", ["Yaz", "Kış"]),
        ):
            put_values(
                owner_api,
                product,
                [
                    {"attribute_id": self.kumas["id"], "option_ids": [self.k[kumas]]},
                    {
                        "attribute_id": self.sezon["id"],
                        "option_ids": [self.s[s] for s in sezon],
                    },
                    {
                        "attribute_id": self.gizli["id"],
                        "option_ids": [self.gizli["options"][0]["id"]],
                    },
                ],
            )


def listing(client, *options):
    params = {"option": list(options)}
    body = client.get(STORE, params).json()
    return {i["id"] for i in body["items"]}, body["facets"]["attributes"]


def counts(facets):
    return {f["name"]: {o["label"]: o["count"] for o in f["options"]} for f in facets}


def test_filterable_attributes_become_facets_with_counts(owner_api, client):
    fx = Fixture(owner_api)
    ids, facets = listing(client)
    assert ids == {fx.a.pk, fx.b.pk, fx.c.pk, fx.d.pk}
    assert counts(facets) == {
        "Kumaş": {"Pamuk": 2, "Viskon": 1},
        "Sezon": {"Yaz": 2, "Kış": 2},
    }
    assert facets[0]["product_class"] == "Giyim"


def test_options_of_one_attribute_are_or_and_attributes_are_and(owner_api, client):
    fx = Fixture(owner_api)
    ids, _ = listing(client, fx.k["Pamuk"], fx.k["Viskon"])
    assert ids == {fx.a.pk, fx.b.pk, fx.c.pk}
    ids, _ = listing(client, fx.k["Pamuk"], fx.s["Yaz"])
    assert ids == {fx.a.pk}
    ids, _ = listing(client, fx.k["Viskon"], fx.s["Yaz"], fx.s["Kış"])
    assert ids == {fx.c.pk}


def test_an_attribute_facet_ignores_its_own_choice_but_respects_the_others(
    owner_api,
    client,
):
    fx = Fixture(owner_api)
    _, facets = listing(client, fx.k["Pamuk"], fx.s["Yaz"])
    assert counts(facets) == {
        "Kumaş": {"Pamuk": 1, "Viskon": 1},  # Yaz only: A (pamuk), C (viskon)
        "Sezon": {"Yaz": 1, "Kış": 1},  # Pamuk only: A (yaz), B (kış)
    }


def test_unknown_option_ids_are_ignored(owner_api, client):
    fx = Fixture(owner_api)
    ids, _ = listing(client, 999999)
    assert ids == {fx.a.pk, fx.b.pk, fx.c.pk, fx.d.pk}


def test_filters_combine_with_the_existing_ones(owner_api, client):
    fx = Fixture(owner_api)
    body = client.get(
        STORE,
        {"option": [fx.k["Pamuk"]], "q": "A", "in_stock": "true"},
    ).json()
    assert {i["id"] for i in body["items"]} <= {fx.a.pk, fx.b.pk}


def test_draft_products_do_not_count_in_facets(owner_api, client):
    fx = Fixture(owner_api)
    ProductProfile.objects.filter(product=fx.a).update(status=ProductStatus.DRAFT)
    _, facets = listing(client)
    assert counts(facets)["Kumaş"] == {"Pamuk": 1, "Viskon": 1}


def test_values_helper_lists_text_and_options(owner_api):
    kumas = define(owner_api, options=["Pamuk"])
    product = make_product()
    attributes.set_values(
        product,
        [attributes.ValueInput(kumas["id"], None, (option_ids(kumas)["Pamuk"],))],
    )
    assert attributes.values_for(product) == [
        {
            "attribute_id": kumas["id"],
            "text": None,
            "option_ids": [option_ids(kumas)["Pamuk"]],
        },
    ]
