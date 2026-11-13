"""Public crawler files. Django knows the catalogue; the storefront serves these on the
one public origin by proxying `/robots.txt` and `/sitemap.xml` here."""

from __future__ import annotations

from xml.sax.saxutils import escape

from django.http import HttpResponse
from django.views.decorators.http import require_GET

from store_backend.catalog.models import ProductProfile
from store_backend.catalog.models import ProductStatus
from store_backend.services import categories

DISALLOWED = (
    "/admin",
    "/sepet",
    "/odeme",
    "/hesabim",
    "/siparis-takip",
    "/api",
)


def public_origin(request) -> str:
    """The shop's public origin as forwarded by the storefront (Django is private)."""
    proto = request.headers.get("x-forwarded-proto", request.scheme).split(",")[0]
    host = request.headers.get("x-forwarded-host", request.get_host()).split(",")[0]
    return f"{proto.strip()}://{host.strip()}"


@require_GET
def robots_txt(request):
    lines = ["User-agent: *", "Allow: /"]
    lines += [f"Disallow: {path}" for path in DISALLOWED]
    lines.append(f"Sitemap: {public_origin(request)}/sitemap.xml")
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")


@require_GET
def sitemap_xml(request):
    origin = public_origin(request)
    entries: list[tuple[str, str | None]] = [("/", None)]
    entries += [
        (f"/kategori/{slug}", None)
        for slug in categories.visible().order_by("path").values_list("slug", flat=True)
    ]
    products = (
        ProductProfile.objects.filter(status=ProductStatus.PUBLISHED)
        .order_by("slug")
        .values_list("slug", "product__date_updated")
    )
    entries += [
        (f"/urun/{slug}", updated.date().isoformat()) for slug, updated in products
    ]
    body = "".join(
        f"<url><loc>{escape(origin + path)}</loc>"
        + (f"<lastmod>{lastmod}</lastmod>" if lastmod else "")
        + "</url>"
        for path, lastmod in entries
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{body}</urlset>"
    )
    return HttpResponse(xml, content_type="application/xml")
