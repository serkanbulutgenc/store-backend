# ruff: noqa: PLR2004
from http import HTTPStatus

import pytest
from django.test import Client
from oscar.apps.order.models import Order

from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.conftest import make_verified_user
from store_backend.services import reviews
from store_backend.shop.models import ReturnLine
from store_backend.shop.models import ReturnRequest
from store_backend.shop.models import ReturnStatus
from store_backend.shop.models import Review
from store_backend.shop.tests.conftest import Guest
from store_backend.shop.tests.test_checkout import CHECKOUT
from store_backend.shop.tests.test_checkout import fill
from store_backend.shop.tests.test_checkout import payload
from store_backend.shop.tests.test_orders import act
from store_backend.staff.models import AuditEntry

pytestmark = pytest.mark.django_db

ADMIN = "/api/admin/reviews"


@pytest.fixture
def me(customer, log_in):
    return Guest(log_in(Client(), customer))


@pytest.fixture
def staff(staff_user, api_for):
    return api_for(staff_user)


def mine(product):
    return f"/api/store/account/reviews/{product.pk}"


def public(product):
    return f"/api/store/products/{product.profile.slug}/reviews"


def my_state(product):
    return f"/api/store/products/{product.profile.slug}/my-review"


def deliver_to(buyer, customer, variant, staff):
    fill(buyer, variant, quantity=2)
    placed = buyer.post(CHECKOUT, payload(email=customer.email)).json()
    number = placed["number"]
    act(staff, number, "pay", {"amount": "200.00"})
    act(staff, number, "ship", {"carrier": "Aras", "tracking_number": "1"})
    assert act(staff, number, "deliver").status_code == HTTPStatus.OK
    return Order.objects.get(number=number)


@pytest.fixture
def bought(me, customer, shop, staff):
    product, variants = shop
    order = deliver_to(me, customer, variants["M"], staff)
    return product, order


def return_line(order, status, quantity):
    request = ReturnRequest.objects.create(
        order=order,
        status=status,
        reason="wrong_size",
    )
    ReturnLine.objects.create(
        request=request,
        order_line=order.lines.get(),
        quantity=quantity,
    )


# --- eligibility ---------------------------------------------------------------


def test_without_a_delivered_line_the_api_says_why(me, shop):
    product, _ = shop

    state = me.get(my_state(product)).json()
    response = me.put(mine(product), {"stars": 5, "body": ""})

    assert state["can_review"] is False
    assert state["reason"] == "review_not_delivered"
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()["code"] == "review_not_delivered"


def test_an_undelivered_order_does_not_count(me, customer, shop):
    product, variants = shop
    fill(me, variants["M"])
    me.post(CHECKOUT, payload(email=customer.email))

    assert me.put(mine(product), {"stars": 5, "body": ""}).status_code == 403


def test_a_fully_returned_line_removes_eligibility(me, bought):
    product, order = bought
    return_line(order, ReturnStatus.RECEIVED, 2)

    response = me.put(mine(product), {"stars": 5, "body": ""})

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()["code"] == "review_fully_returned"


def test_a_partial_or_pending_or_rejected_return_keeps_eligibility(me, bought):
    product, order = bought
    return_line(order, ReturnStatus.APPROVED, 1)
    assert me.put(mine(product), {"stars": 4, "body": ""}).status_code == 200


def test_a_rejected_return_does_not_remove_eligibility(me, bought):
    product, order = bought
    return_line(order, ReturnStatus.REJECTED, 2)
    assert me.put(mine(product), {"stars": 4, "body": ""}).status_code == 200


def test_guests_get_401_and_dashboard_users_403(shop, staff_user, log_in):
    product, _ = shop
    assert Guest().put(mine(product), {"stars": 5, "body": ""}).status_code == 401
    boss = Guest(log_in(Client(), staff_user))
    assert boss.put(mine(product), {"stars": 5, "body": ""}).status_code == 403


# --- publishing ----------------------------------------------------------------


def test_stars_only_publish_at_once_and_move_the_average(me, bought):
    product, _ = bought

    response = me.put(mine(product), {"stars": 4, "body": ""})

    assert response.status_code == HTTPStatus.OK, response.content
    assert response.json()["status"] == "published"
    page = Guest().get(public(product)).json()
    assert page["average"] == 4.0
    assert page["count"] == 1


def test_a_text_review_is_invisible_until_approved(me, bought, staff):
    product, _ = bought
    response = me.put(mine(product), {"stars": 5, "body": "Çok güzel"})
    review_id = response.json()["id"]

    assert response.json()["status"] == "pending"
    page = Guest().get(public(product)).json()
    assert page["items"] == []
    assert page["count"] == 0
    assert page["average"] is None

    assert staff.post(f"{ADMIN}/{review_id}/approve").status_code == 200
    page = Guest().get(public(product)).json()
    assert page["count"] == 1
    assert page["average"] == 5.0
    assert page["items"][0]["body"] == "Çok güzel"
    assert AuditEntry.objects.filter(action="review_approved").count() == 1


def test_emoji_only_text_also_waits(me, bought):
    product, _ = bought
    response = me.put(mine(product), {"stars": 5, "body": "😍"})
    assert response.json()["status"] == "pending"


def test_editing_the_text_of_an_approved_review_hides_it_again(me, bought, staff):
    product, _ = bought
    review_id = me.put(mine(product), {"stars": 5, "body": "iyi"}).json()["id"]
    staff.post(f"{ADMIN}/{review_id}/approve")

    same = me.put(mine(product), {"stars": 3, "body": "iyi"})
    assert same.json()["status"] == "published"
    changed = me.put(mine(product), {"stars": 3, "body": "iyi ama pahalı"})

    assert changed.json()["status"] == "pending"
    assert Guest().get(public(product)).json()["count"] == 0
    assert Review.objects.count() == 1


def test_clearing_the_text_publishes_the_stars(me, bought):
    product, _ = bought
    me.put(mine(product), {"stars": 5, "body": "iyi"})

    response = me.put(mine(product), {"stars": 5, "body": "  "})

    assert response.json()["status"] == "published"
    assert response.json()["body"] == ""


def test_the_shopper_can_delete_their_review(me, bought):
    product, _ = bought
    me.put(mine(product), {"stars": 5, "body": ""})

    assert me.delete(mine(product)).status_code == HTTPStatus.NO_CONTENT
    assert me.delete(mine(product)).status_code == HTTPStatus.NO_CONTENT
    assert Review.objects.count() == 0


def test_my_review_state_returns_the_review(me, bought):
    product, _ = bought
    me.put(mine(product), {"stars": 2, "body": "idare eder"})

    state = me.get(my_state(product)).json()

    assert state["can_review"] is True
    assert state["review"]["stars"] == 2
    assert state["review"]["status"] == "pending"


# --- validation ----------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    ["<script>alert(1)</script>", "a <b>kalın</b>", "<https://x.test>", "</p>"],
)
def test_html_is_rejected(me, bought, body):
    product, _ = bought
    response = me.put(mine(product), {"stars": 5, "body": body})
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "body" in response.json()["fields"]


def test_stars_must_be_1_to_5(me, bought):
    product, _ = bought
    for stars in (0, 6):
        assert me.put(mine(product), {"stars": stars, "body": ""}).status_code == 422


def test_body_is_capped_at_2000_characters(me, bought):
    product, _ = bought
    assert me.put(mine(product), {"stars": 5, "body": "a" * 2001}).status_code == 422
    assert me.put(mine(product), {"stars": 5, "body": "a" * 2000}).status_code == 200


def test_links_and_images_are_stripped_but_bare_urls_stay():
    cleaned = reviews.clean_body(
        "Bak [burası](https://x.test) ve ![resim](https://x.test/a.png) "
        "https://y.test **kalın**",
    )
    assert cleaned == "Bak burası ve  https://y.test **kalın**"


def test_the_source_is_stored_raw(me, bought):
    product, _ = bought
    me.put(mine(product), {"stars": 5, "body": "**kalın**\n\n- bir\n- iki"})
    assert Review.objects.get().body == "**kalın**\n\n- bir\n- iki"


# --- display -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("Ayşe Kaya", "Ayşe K."),
        ("ayşe  iyi kaya", "ayşe K."),
        ("Ayşe", "Ayşe"),
        ("", "Müşteri"),
        ("Ali İnce", "Ali İ."),
        ("Ali ince", "Ali İ."),
    ],
)
def test_display_name_is_first_name_and_last_initial(name, shown):
    class U:
        pass

    user = U()
    user.name = name
    assert reviews.display_name(user) == shown


def test_cards_and_product_page_show_average_and_count(me, bought):
    product, _ = bought
    me.put(mine(product), {"stars": 4, "body": ""})
    other = make_verified_user()
    Review.objects.create(user=other, product=product, stars=5, status="published")
    Review.objects.create(
        user=make_verified_user(),
        product=product,
        stars=1,
        body="kötü",
        status="pending",
    )

    detail = Guest().get(f"/api/store/products/{product.profile.slug}").json()
    listing = Guest().get("/api/store/products").json()["items"][0]

    assert detail["rating_average"] == 4.5
    assert detail["rating_count"] == 2
    assert listing["rating_average"] == 4.5
    assert listing["rating_count"] == 2


def test_a_product_without_reviews_has_no_average(shop):
    product, _ = shop
    detail = Guest().get(f"/api/store/products/{product.profile.slug}").json()
    assert detail["rating_average"] is None
    assert detail["rating_count"] == 0


def test_only_published_products_have_reviews(me, bought):
    product, _ = bought
    ProductProfile.objects.filter(product=product).update(status=ProductStatus.DRAFT)
    assert Guest().get(public(product)).status_code == HTTPStatus.NOT_FOUND


# --- moderation ----------------------------------------------------------------


def test_the_queue_lists_by_status_with_source_and_author(me, bought, staff):
    product, _ = bought
    me.put(mine(product), {"stars": 5, "body": "**harika**"})

    body = staff.get(f"{ADMIN}?status=pending").json()

    assert body["total"] == 1
    row = body["items"][0]
    assert row["body"] == "**harika**"
    assert row["product_title"] == product.title
    assert row["status"] == "pending"
    assert staff.get(f"{ADMIN}?status=published").json()["total"] == 0


def test_reject_and_delete_are_audited_and_correctable(me, bought, staff):
    product, _ = bought
    review_id = me.put(mine(product), {"stars": 5, "body": "x y z"}).json()["id"]

    assert staff.post(f"{ADMIN}/{review_id}/reject").json()["status"] == "rejected"
    assert Guest().get(public(product)).json()["count"] == 0
    assert staff.post(f"{ADMIN}/{review_id}/approve").json()["status"] == "published"
    assert staff.delete(f"{ADMIN}/{review_id}").status_code == HTTPStatus.NO_CONTENT

    actions = list(
        AuditEntry.objects.filter(action__startswith="review_").values_list(
            "action",
            flat=True,
        ),
    )
    assert sorted(actions) == ["review_approved", "review_deleted", "review_rejected"]
    assert not Review.objects.exists()


def test_owner_can_moderate_too(me, bought, owner, api_for):
    product, _ = bought
    review_id = me.put(mine(product), {"stars": 5, "body": "iyi"}).json()["id"]
    boss = api_for(owner, enrol=True)
    assert boss.post(f"{ADMIN}/{review_id}/approve").status_code == 200


def test_unknown_review_is_404(staff):
    assert staff.post(f"{ADMIN}/999999/approve").status_code == HTTPStatus.NOT_FOUND


def test_anonymisation_removes_reviews(me, bought, customer):
    from store_backend.services import customers  # noqa: PLC0415

    product, _ = bought
    me.put(mine(product), {"stars": 5, "body": ""})

    customers.anonymise(customer)

    assert not Review.objects.exists()
