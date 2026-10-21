from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render

from apps.catalog.models import Asset, Product, Variant
from apps.costing.models import Channel, GlobalParams, PriceByChannel, Printer
from apps.inventory.models import FilamentSKU
from apps.production.models import PrintJob, ProductionOrder


@login_required
def dashboard(request):
    products = Product.objects.exclude(status=Product.Status.DISCONTINUED)
    products_without_photo = products.annotate(n=Count("assets", filter=Q(assets__kind=Asset.Kind.PHOTO))).filter(n=0)
    variants_without_cost = Variant.objects.filter(cost_cached__isnull=True, product__in=products)
    parts_without_3mf = [
        p for prod in products.prefetch_related("parts__assets") for p in prod.parts.all()
        if not p.production_assets().exists()
    ]
    low_filaments = [f for f in FilamentSKU.objects.filter(active=True) if f.below_minimum]
    below_margin = PriceByChannel.objects.filter(below_minimum=True).order_by("-created_at")[:20]
    open_orders = ProductionOrder.objects.exclude(status__in=[ProductionOrder.Status.READY, ProductionOrder.Status.CANCELLED])
    printing = PrintJob.objects.filter(result=PrintJob.Result.PRINTING).select_related("order", "part", "printer")
    context = {
        "params": GlobalParams.current(),
        "counts": {
            "products": products.count(),
            "variants": Variant.objects.filter(product__in=products).count(),
            "filaments": FilamentSKU.objects.filter(active=True).count(),
            "printers": Printer.objects.filter(status=Printer.Status.ACTIVE).count(),
            "channels": Channel.objects.filter(status=Channel.Status.ACTIVE).count(),
            "orders": open_orders.count(),
        },
        "products_without_photo": products_without_photo[:10],
        "variants_without_cost": variants_without_cost.select_related("product")[:10],
        "parts_without_3mf": parts_without_3mf[:10],
        "low_filaments": low_filaments[:10],
        "below_margin": below_margin,
        "printing": printing,
        "open_orders": open_orders.select_related("variant__product")[:10],
    }
    return render(request, "core/dashboard.html", context)


def healthz(request):
    """Verificação de saúde para a hospedagem: responde 200 se o banco responde."""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"status": "erro", "detail": str(exc)[:200]}, status=503)
