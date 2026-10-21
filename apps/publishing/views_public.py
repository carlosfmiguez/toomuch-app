"""
Catálogo público (sem login): lista por categoria e página do produto no modelo
desenhado. Mostra só produtos prontos para catálogo, com o preço do canal Site.
Não há checkout: o botão leva ao WhatsApp e, quando houver, ao anúncio no marketplace.

Cache: a lista de produtos prontos é cara (checklist por produto); fica em cache por
5 minutos e é invalidada quando um produto, preço, foto ou arquivo muda (signals em
apps.publishing.signals).
"""

from __future__ import annotations

from urllib.parse import quote

from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from apps.catalog.models import Asset, Category, Product
from apps.costing.models import Channel

from .catalog import build_product_page
from .models import Publication

WHATSAPP = getattr(settings, "TOOMUCH_WHATSAPP", "")  # ex.: 5548999999999
INSTAGRAM = getattr(settings, "TOOMUCH_INSTAGRAM", "toomuchbr.art")


def _site_channel() -> Channel | None:
    return Channel.objects.filter(kind=Channel.Kind.SITE, status=Channel.Status.ACTIVE).first() \
        or Channel.objects.filter(status=Channel.Status.ACTIVE).order_by("order").first()


PUBLIC_IDS_KEY = "public:ready-product-ids"


def _public_products():
    from django.core.cache import cache

    ids = cache.get(PUBLIC_IDS_KEY)
    qs = Product.objects.filter(status__in=[Product.Status.ACTIVE, Product.Status.SEASONAL]).select_related("category").order_by("category__order", "name")
    if ids is None:
        ready = [p for p in qs if p.ready_for_catalog]
        cache.set(PUBLIC_IDS_KEY, [p.pk for p in ready], 300)
        return ready
    return [p for p in qs if p.pk in set(ids)]


def invalidate_public_cache(*_args, **_kwargs):
    from django.core.cache import cache

    cache.delete(PUBLIC_IDS_KEY)


def whatsapp_link(text: str) -> str:
    if not WHATSAPP:
        return "#"
    return f"https://wa.me/{WHATSAPP}?text={quote(text)}"


def _base_context():
    return {
        "whatsapp": whatsapp_link("Olá! Vi o catálogo da Toomuch e quero saber mais."),
        "instagram": INSTAGRAM,
        "categories": Category.objects.all(),
    }


def home(request):
    channel = _site_channel()
    products = _public_products()
    pages = [build_product_page(p, channel) for p in products]
    by_category: list[tuple[Category, list]] = []
    for page in pages:
        if by_category and by_category[-1][0].pk == page.product.category_id:
            by_category[-1][1].append(page)
        else:
            by_category.append((page.product.category, [page]))
    return render(request, "public/home.html", {**_base_context(), "by_category": by_category, "featured": pages[:3]})


def category(request, code):
    cat = get_object_or_404(Category, code__iexact=code)
    channel = _site_channel()
    pages = [build_product_page(p, channel) for p in _public_products() if p.category_id == cat.pk]
    return render(request, "public/category.html", {**_base_context(), "category": cat, "pages": pages})


def product(request, sku_base):
    prod = get_object_or_404(Product.objects.select_related("category"), sku_base__iexact=sku_base)
    if not prod.ready_for_catalog:
        raise Http404
    channel = _site_channel()
    page = build_product_page(prod, channel)
    photos = list(prod.assets.filter(kind__in=[Asset.Kind.PHOTO, Asset.Kind.RENDER]).order_by("-is_main", "order"))
    related = [build_product_page(p, channel) for p in _public_products() if p.pk != prod.pk and p.category_id == prod.category_id][:3]
    if len(related) < 3:
        related += [build_product_page(p, channel) for p in _public_products() if p.pk != prod.pk and p.category_id != prod.category_id][: 3 - len(related)]
    ml = Publication.objects.filter(variant__product=prod, destination=Publication.Destination.ML, status=Publication.Status.LIVE).exclude(external_id="").first()
    ask = whatsapp_link(f"Olá! Quero a {prod.name} ({prod.sku_base}). Pode me passar as cores disponíveis e o prazo?")
    return render(request, "public/product.html", {
        **_base_context(), "page": page, "photos": photos, "related": related, "ask": ask,
        "ml_url": f"https://www.mercadolivre.com.br/{ml.external_id}" if ml else "",
    })
