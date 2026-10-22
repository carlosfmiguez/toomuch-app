"""
Dados do painel (índice do admin) e helpers do shell para as telas próprias.

`dashboard_callback` é chamado pelo Unfold ao renderizar admin/index.html.
`shell` monta o contexto que as telas próprias precisam para usar o layout do
Unfold (sidebar, cabeçalho, menu do usuário): o contexto do admin site + título.
"""

from __future__ import annotations

from django.contrib import admin
from django.db.models import Count, Q
from django.urls import reverse

from apps.catalog.models import Asset, Product, Variant
from apps.costing.models import Channel, GlobalParams, PriceByChannel, Printer
from apps.inventory.models import FilamentSKU
from apps.production.models import PrintJob, ProductionOrder


def shell(request, title: str, **extra) -> dict:
    ctx = admin.site.each_context(request)
    ctx.update({"title": title, "is_nav_sidebar_enabled": True, "has_permission": request.user.is_active and request.user.is_staff})
    ctx.update(extra)
    return ctx


def active_products():
    return Product.objects.exclude(status=Product.Status.DISCONTINUED)


def pending_items() -> list[dict]:
    products = active_products().prefetch_related("parts__assets", "variants")
    items: list[dict] = []
    for prod in products:
        for part in prod.parts.all():
            if not part.production_assets().exists():
                items.append({"kind": "Peça sem 3MF fatiado", "label": f"{prod.name} · {part.name}", "url": reverse("admin:catalog_part_change", args=[part.pk])})
    for v in Variant.objects.filter(cost_cached__isnull=True, product__in=products).select_related("product"):
        items.append({"kind": "Variante sem custo", "label": v.sku, "url": reverse("catalog:product_detail", args=[v.product_id])})
    for prod in products.annotate(n=Count("assets", filter=Q(assets__kind=Asset.Kind.PHOTO))).filter(n=0):
        items.append({"kind": "Produto sem foto", "label": prod.name, "url": reverse("catalog:product_detail", args=[prod.pk])})
    return items


def pending_count() -> int:
    return len(pending_items())


def dashboard_callback(request, context: dict) -> dict:
    products = active_products()
    open_orders = ProductionOrder.objects.exclude(status__in=[ProductionOrder.Status.READY, ProductionOrder.Status.CANCELLED])
    printing = PrintJob.objects.filter(result=PrintJob.Result.PRINTING).select_related("order__variant__product", "part", "printer")
    low = [f for f in FilamentSKU.objects.filter(active=True) if f.below_minimum]
    ready = sum(1 for p in products if p.ready_for_catalog)
    params = GlobalParams.current()
    pend = pending_items()
    context.update({
        "kpis": [
            {"title": "Produtos ativos", "value": products.count(), "footer": f"{ready} prontos para o catálogo", "icon": "deployed_code", "url": reverse("catalog:product_list")},
            {"title": "Variantes", "value": Variant.objects.filter(product__in=products).count(), "footer": f"{Variant.objects.filter(product__in=products, cost_cached__isnull=False).count()} com custo", "icon": "style", "url": reverse("costing:price_list")},
            {"title": "Ordens abertas", "value": open_orders.count(), "footer": f"{printing.count()} imprimindo agora", "icon": "precision_manufacturing", "url": reverse("production:board")},
            {"title": "Filamentos", "value": FilamentSKU.objects.filter(active=True).count(), "footer": f"{len(low)} abaixo do mínimo", "icon": "gradient", "url": reverse("admin:inventory_filamentsku_changelist"), "warn": bool(low)},
        ],
        "params": params,
        "pending": pend[:12],
        "pending_total": len(pend),
        "low_filaments": low[:8],
        "below_margin": PriceByChannel.objects.filter(below_minimum=True).select_related("variant", "channel").order_by("-created_at")[:8],
        "printing": printing[:8],
        "open_orders": open_orders.select_related("variant__product", "printer").order_by("priority", "due_date")[:8],
        "channels": Channel.objects.filter(status=Channel.Status.ACTIVE).count(),
        "printers": Printer.objects.filter(status=Printer.Status.ACTIVE).count(),
    })
    return context
