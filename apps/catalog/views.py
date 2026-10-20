from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.costing.services import compute_prices, compute_variant_cost, latest_prices

from .models import Asset, Category, Product, Variant


@login_required
def product_list(request):
    qs = Product.objects.select_related("category").prefetch_related("variants")
    status = request.GET.get("status")
    category = request.GET.get("categoria")
    q = request.GET.get("q", "").strip()
    if status:
        qs = qs.filter(status=status)
    if category:
        qs = qs.filter(category__code=category)
    if q:
        qs = qs.filter(name__icontains=q) | qs.filter(sku_base__icontains=q)
    products = []
    for p in qs:
        variants = list(p.variants.all())
        costs = [v.cost_cached for v in variants if v.cost_cached is not None]
        products.append({
            "obj": p, "variants": len(variants), "cost_min": min(costs) if costs else None,
            "cost_max": max(costs) if costs else None, "photo": p.main_photo, "ready": p.ready_for_catalog,
        })
    return render(request, "catalog/product_list.html", {
        "products": products, "categories": Category.objects.all(), "statuses": Product.Status.choices,
        "q": q, "status": status, "category": category,
    })


@login_required
def product_detail(request, pk):
    product = get_object_or_404(Product.objects.select_related("category"), pk=pk)
    variants = []
    for v in product.variants.select_related("primary_color", "secondary_color", "main_filament", "secondary_filament"):
        variants.append({"obj": v, "prices": latest_prices(v), "bom": v.bom_items.select_related("part", "component")})
    parts = []
    for part in product.parts.all():
        parts.append({"obj": part, "files": part.assets.filter(kind__in=[Asset.Kind.THREEMF_SLICED, Asset.Kind.STL, Asset.Kind.STEP, Asset.Kind.F3D, Asset.Kind.THREEMF_PROJECT])})
    photos = product.assets.filter(kind__in=[Asset.Kind.PHOTO, Asset.Kind.RENDER]).order_by("-is_main", "order")
    return render(request, "catalog/product_detail.html", {
        "product": product, "variants": variants, "parts": parts, "photos": photos,
        "checklist": product.catalog_checklist(),
    })


@login_required
@require_POST
def variant_recalculate(request, pk):
    variant = get_object_or_404(Variant, pk=pk)
    _, problems = compute_variant_cost(variant)
    compute_prices(variant)
    for p in problems:
        messages.warning(request, f"{variant.sku}: {p.message}")
    messages.success(request, f"{variant.sku}: custo R$ {variant.cost_cached} e preços sugeridos recalculados.")
    return redirect("catalog:product_detail", pk=variant.product_id)
