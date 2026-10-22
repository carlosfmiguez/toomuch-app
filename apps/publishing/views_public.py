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

from apps.catalog.models import Asset, Category, Collection, Product
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
    qs = (Product.objects.filter(status__in=[Product.Status.ACTIVE, Product.Status.SEASONAL])
          .select_related("category").prefetch_related("collections").order_by("category__order", "name"))
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


def _visible_collections(p: Product) -> list[Collection]:
    return [c for c in p.collections.all() if c.show_on_site]


def _collection_tiles(products) -> list[dict]:
    """Coleções visíveis que têm ao menos uma peça publicada, com a foto da primeira peça."""
    tiles: dict[int, dict] = {}
    for p in products:
        for c in _visible_collections(p):
            tile = tiles.setdefault(c.pk, {"collection": c, "count": 0, "photo": None})
            tile["count"] += 1
            if tile["photo"] is None:
                tile["photo"] = p.main_photo
    return sorted(tiles.values(), key=lambda t: (t["collection"].order, t["collection"].name))


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
    return render(request, "public/home.html", {
        **_base_context(), "by_category": by_category, "featured": pages[:3], "collection_tiles": _collection_tiles(products),
    })


def category(request, code):
    cat = get_object_or_404(Category, code__iexact=code)
    channel = _site_channel()
    in_cat = [p for p in _public_products() if p.category_id == cat.pk]
    # filtro por coleção: só as coleções que têm peça nesta categoria
    chips = {c.pk: c for p in in_cat for c in _visible_collections(p)}
    chips = sorted(chips.values(), key=lambda c: (c.order, c.name))
    picked = request.GET.get("colecao", "")
    if picked:
        in_cat = [p for p in in_cat if any(c.slug == picked for c in _visible_collections(p))]
    pages = [build_product_page(p, channel) for p in in_cat]
    return render(request, "public/category.html", {
        **_base_context(), "category": cat, "pages": pages, "chips": chips, "picked": picked,
    })


def collection(request, slug):
    col = get_object_or_404(Collection, slug=slug, show_on_site=True)
    channel = _site_channel()
    products = [p for p in _public_products() if any(c.pk == col.pk for c in p.collections.all())]
    pages = [build_product_page(p, channel) for p in products]
    return render(request, "public/collection.html", {**_base_context(), "collection": col, "pages": pages})


def product(request, sku_base):
    prod = get_object_or_404(Product.objects.select_related("category"), sku_base__iexact=sku_base)
    if not prod.ready_for_catalog:
        raise Http404
    channel = _site_channel()
    page = build_product_page(prod, channel)
    photos = list(prod.assets.filter(kind__in=[Asset.Kind.PHOTO, Asset.Kind.RENDER]).order_by("-is_main", "order"))
    cols = _visible_collections(prod)
    col_ids = {c.pk for c in cols}
    others = [p for p in _public_products() if p.pk != prod.pk]
    # se a peça tem coleção, sugere a mesma linha de design (qualquer categoria); senão, a mesma categoria e depois o resto
    same_col = [p for p in others if col_ids & {c.pk for c in p.collections.all()}]
    if same_col:
        picks = same_col[:4]
        related_title = f"Da coleção {cols[0].name}" if len(cols) == 1 else "Da mesma coleção"
    else:
        picks = sorted(others, key=lambda p: p.category_id != prod.category_id)[:3]
        related_title = "Você também vai gostar"
    related = [build_product_page(p, channel) for p in picks]
    ml = Publication.objects.filter(variant__product=prod, destination=Publication.Destination.ML, status=Publication.Status.LIVE).exclude(external_id="").first()
    ask = whatsapp_link(f"Olá! Quero a {prod.name} ({prod.sku_base}). Pode me passar as cores disponíveis e o prazo?")
    return render(request, "public/product.html", {
        **_base_context(), "page": page, "photos": photos, "related": related, "ask": ask,
        "collections": cols, "related_title": related_title,
        "ml_url": f"https://www.mercadolivre.com.br/{ml.external_id}" if ml else "",
    })
