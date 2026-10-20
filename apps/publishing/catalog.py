"""
Catálogo PDF e HTML gerado a partir da ficha dos produtos, no modelo visual da marca
(capa, sumário e uma página A4 por produto).
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.files.storage import default_storage
from django.template.loader import render_to_string
from django.utils import timezone

from apps.catalog.models import Asset, Category, Product, Variant
from apps.costing.models import Channel
from apps.costing.services import latest_prices

LOCAL_BASE = "http://toomuch.local/"


@dataclass
class PricedVariant:
    variant: Variant
    price: Decimal | None
    approved: bool
    color_name: str
    color_hex: str


@dataclass
class ProductPage:
    product: Product
    photo: Asset | None
    variants: list[PricedVariant]
    price_from: Decimal | None
    price_approved: bool
    page_number: int = 0
    dims: str = ""
    light: str = ""
    colors: list[PricedVariant] = field(default_factory=list)


@dataclass
class CatalogDocument:
    title: str
    channel: Channel | None
    year: int
    cover: Asset | None
    cover_product: Product | None
    categories: list[tuple[Category, int]]
    tiles: list[tuple[Category, Asset | None]]
    pages: list[ProductPage]
    generated_at: object


def _price_for(variant: Variant, channel: Channel | None) -> tuple[Decimal | None, bool]:
    if channel is None:
        return None, False
    for row in latest_prices(variant):
        if row.channel_id == channel.pk:
            return row.current_price, row.approved
    return None, False


def build_product_page(product: Product, channel: Channel | None) -> ProductPage:
    variants: list[PricedVariant] = []
    for v in product.variants.select_related("primary_color").all():
        price, approved = _price_for(v, channel)
        variants.append(PricedVariant(
            variant=v, price=price, approved=approved,
            color_name=v.primary_color.name if v.primary_color else "",
            color_hex=v.primary_color.hex if v.primary_color and v.primary_color.hex else "",
        ))
    priced = [pv for pv in variants if pv.price is not None]
    price_from = min(pv.price for pv in priced) if priced else None
    price_approved = bool(priced) and all(pv.approved for pv in priced)
    dims = []
    if product.height_mm:
        dims.append(f"{product.height_mm / 10:g} cm de altura")
    if product.width_mm:
        dims.append(f"{product.width_mm / 10:g} cm de largura" if product.width_mm != product.depth_mm else f"{product.width_mm / 10:g} cm de diâmetro")
    if product.depth_mm and product.depth_mm != product.width_mm:
        dims.append(f"{product.depth_mm / 10:g} cm de profundidade")
    if product.weight_g:
        dims.append(f"{product.weight_g} g")
    # cores distintas por nome
    seen: set[str] = set()
    colors = []
    for pv in variants:
        if pv.color_name and pv.color_name not in seen:
            seen.add(pv.color_name)
            colors.append(pv)
    return ProductPage(
        product=product, photo=product.main_photo, variants=variants, price_from=price_from,
        price_approved=price_approved, dims=" · ".join(dims), colors=colors,
    )


def build_catalog(channel: Channel | None, only_ready: bool = True, year: int | None = None) -> CatalogDocument:
    qs = Product.objects.filter(status__in=[Product.Status.ACTIVE, Product.Status.SEASONAL]).select_related("category").order_by("category__order", "name")
    products = [p for p in qs if (p.ready_for_catalog if only_ready else p.license_is_valid)]
    pages: list[ProductPage] = []
    categories: list[tuple[Category, int]] = []
    number = 3  # capa = 1, sumário = 2
    current_category = None
    for product in products:
        page = build_product_page(product, channel)
        page.page_number = number
        if product.category_id != current_category:
            categories.append((product.category, number))
            current_category = product.category_id
        pages.append(page)
        number += 1
    cover_page = next((p for p in pages if p.photo), None)
    tiles: list[tuple[Category, Asset | None]] = []
    for category, _number in categories[:4]:
        first = next((p for p in pages if p.product.category_id == category.pk), None)
        tiles.append((category, first.photo if first else None))
    return CatalogDocument(
        title=f"Catálogo {year or timezone.localdate().year}", channel=channel, year=year or timezone.localdate().year,
        cover=cover_page.photo if cover_page else None, cover_product=cover_page.product if cover_page else None,
        categories=categories, tiles=tiles, pages=pages, generated_at=timezone.localtime(),
    )


def render_catalog_html(doc: CatalogDocument, for_pdf: bool = True, only_products: bool = False) -> str:
    return render_to_string("publishing/catalog_pdf.html", {"doc": doc, "for_pdf": for_pdf, "only_products": only_products})


def _local_file(url: str):
    """Resolve /static/ e /media/ sem passar pela rede. Retorna (arquivo, mime) ou None."""
    parsed = urlparse(url)
    path = unquote(parsed.path)
    if not (url.startswith(LOCAL_BASE) or parsed.scheme in ("", "file")):
        return None
    static_prefix = "/" + settings.STATIC_URL.strip("/") + "/"
    media_prefix = "/" + settings.MEDIA_URL.strip("/") + "/"
    if path.startswith(static_prefix):
        rel = path[len(static_prefix):]
        found = finders.find(rel) or str(Path(settings.STATIC_ROOT) / rel)
        return open(found, "rb"), mimetypes.guess_type(found)[0]
    if path.startswith(media_prefix):
        rel = path[len(media_prefix):]
        return default_storage.open(rel, "rb"), mimetypes.guess_type(rel)[0]
    return None


def _make_fetcher():
    try:  # WeasyPrint >= 66: classe URLFetcher
        from weasyprint.urls import URLFetcher, URLFetcherResponse

        class LocalFirstFetcher(URLFetcher):
            def fetch(self, url, headers=None):
                found = _local_file(url)
                if found:
                    file_obj, mime = found
                    return URLFetcherResponse(url, file_obj, {"Content-Type": mime or "application/octet-stream"})
                return super().fetch(url, headers)

        return LocalFirstFetcher()
    except ImportError:  # WeasyPrint <= 65: função que devolve dict
        from weasyprint import default_url_fetcher

        def fetcher(url):
            found = _local_file(url)
            if found:
                file_obj, mime = found
                return {"file_obj": file_obj, "mime_type": mime}
            return default_url_fetcher(url)

        return fetcher


def render_catalog_pdf(doc: CatalogDocument, only_products: bool = False) -> bytes:
    from weasyprint import HTML

    html = render_catalog_html(doc, for_pdf=True, only_products=only_products)
    return HTML(string=html, base_url=LOCAL_BASE, url_fetcher=_make_fetcher()).write_pdf()


def register_publications(doc: CatalogDocument, user=None) -> int:
    from .models import Publication

    n = 0
    for page in doc.pages:
        for pv in page.variants:
            Publication.objects.create(
                variant=pv.variant, destination=Publication.Destination.PDF, channel=doc.channel,
                price_sent=pv.price, status=Publication.Status.SENT, published_at=timezone.now(),
                note=f"{doc.title} · página {page.page_number}",
            )
            n += 1
    return n
