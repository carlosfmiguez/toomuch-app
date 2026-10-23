"""
Telas de produto: lista e a ficha do produto, que é onde todo o cadastro acontece
(dados, peças e 3MF, variantes, custo, aprovação de preço, fotos e publicação).
O admin continua existindo como modo avançado.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.core.dashboard import shell
from apps.costing.services import approve_price, latest_prices
from apps.inventory.models import FilamentSKU

from . import workflow
from .forms import (
    ComponentLineForm, PartFileForm, PartForm, ProductCreateForm, ProductDataForm, VariantCreateForm, VariantForm,
)
from .importer import suggest_sku
from .models import Asset, BOMItem, Category, Collection, Part, Product, Variant
from .services import parse_asset

MODEL_KINDS = [Asset.Kind.THREEMF_SLICED, Asset.Kind.STL, Asset.Kind.STEP, Asset.Kind.F3D, Asset.Kind.THREEMF_PROJECT]


def _ficha(product_id: int, step: str = "", frag: str = "") -> str:
    """Endereço da ficha aberta numa etapa do assistente (e, opcionalmente, num ponto dela)."""
    url = reverse("catalog:product_detail", args=[product_id])
    if step:
        url += f"?etapa={step}"
    return f"{url}#{frag}" if frag else url


def _safe_next(request, fallback: str) -> str:
    nxt = request.POST.get("next") or request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return fallback


def _form_errors(request, form) -> None:
    for field, errs in form.errors.items():
        label = form.fields[field].label if field in form.fields else ""
        for e in errs:
            messages.error(request, f"{label}: {e}" if label else e)


# --- lista -----------------------------------------------------------------

@login_required
def product_list(request):
    qs = Product.objects.select_related("category").prefetch_related("variants", "parts", "collections")
    status = request.GET.get("status")
    category = request.GET.get("categoria")
    collection = request.GET.get("colecao")
    q = request.GET.get("q", "").strip()
    if status:
        qs = qs.filter(status=status)
    if category:
        qs = qs.filter(category__code=category)
    if collection:
        qs = qs.filter(collections__slug=collection).distinct()
    if q:
        qs = qs.filter(name__icontains=q) | qs.filter(sku_base__icontains=q)
    products = []
    for p in qs:
        steps = workflow.steps_for(p)
        done = sum(1 for s in steps if s.done)
        nxt = workflow.next_step(steps)
        site_prices = []
        for v in p.variants.all():
            for row in latest_prices(v):
                if row.channel.kind == "site":
                    site_prices.append(row.current_price)
        products.append({
            "obj": p, "variants": p.variants.count(), "photo": p.main_photo, "steps": steps, "done": done,
            "total": len(steps), "next": nxt, "price_min": min(site_prices) if site_prices else None,
        })
    return render(request, "catalog/product_list.html", {
        **shell(request, "Produtos"),
        "products": products, "categories": Category.objects.all(), "statuses": Product.Status.choices,
        "q": q, "status": status, "category": category,
        "collections": Collection.objects.all(), "collection": collection,
    })


# --- criar -----------------------------------------------------------------

@login_required
def product_create(request):
    form = ProductCreateForm(request.POST or None, initial={"origin": Product.Origin.OWN})
    if request.method == "POST" and form.is_valid():
        product = form.save(commit=False)
        if not product.sku_base:
            taken = set(Product.objects.values_list("sku_base", flat=True))
            product.sku_base = suggest_sku(product.name, product.category.code, taken)
        product.status = Product.Status.DRAFT
        product.save()
        Part.objects.create(product=product, name="Peça única")
        messages.success(request, f"{product.name} criado com o SKU-base {product.sku_base}. Complete os dados e avance.")
        return redirect(_ficha(product.pk, "dados"))
    return render(request, "catalog/product_create.html", {**shell(request, "Novo produto"), "form": form})


# --- ficha -----------------------------------------------------------------

@login_required
def product_detail(request, pk):
    product = get_object_or_404(Product.objects.select_related("category"), pk=pk)
    steps = workflow.steps_for(product)
    parts = []
    for part in product.all_parts():
        files = list(part.assets.filter(kind__in=MODEL_KINDS).select_related("printer").order_by("-is_production", "-created_at"))
        prod = next((f for f in files if f.is_production and f.kind == Asset.Kind.THREEMF_SLICED), None)
        shared = part.product_id != product.pk
        used_in = list(part.shared_in.exclude(pk=product.pk)) + ([part.product] if shared else [])
        plate = None
        if prod is not None and part.plate_index and len(prod.plates) > 1:
            pl = prod.plate(part.plate_index)
            if pl:
                plate = {"label": Asset.plate_label(pl), "grams": Asset.plate_grams(pl), "minutes": round(int(pl.get("time_s") or 0) / 60)}
        parts.append({
            "obj": part, "files": files, "production": prod, "form": PartForm(instance=part, prefix=f"p{part.pk}"),
            "shared": shared, "used_in": used_in, "plate": plate,
            "plates_pending": bool(prod and prod.plates_pending),
            "plates_summed": len(prod.plates) if prod and prod.plates_mode == Asset.PLATES_PARTS else 0,
        })
    has_secondary = any(p["obj"].color_slot == Part.ColorSlot.SECONDARY for p in parts)

    # Biblioteca para "usar peça já cadastrada": peças de outros produtos e 3MF soltos.
    library_parts, library_files = [], []
    if request.GET.get("etapa", "") in ("pecas", ""):
        mine = {p["obj"].pk for p in parts}
        for part in Part.objects.exclude(pk__in=mine).select_related("product").order_by("product__name", "name"):
            library_parts.append({"obj": part, "production": part.production_asset()})
        library_files = list(Asset.objects.filter(kind=Asset.Kind.THREEMF_SLICED, part__isnull=True, parse_error="")
                             .select_related("product").order_by("-created_at")[:50])

    variants = []
    for v in product.variants.select_related("primary_color", "secondary_color", "main_filament", "secondary_filament"):
        bom = list(v.bom_items.select_related("part", "component"))
        st = workflow.variant_price_status(v)
        variants.append({
            "obj": v, "parts": [b for b in bom if b.kind == BOMItem.Kind.PART],
            "components": [b for b in bom if b.kind == BOMItem.Kind.COMPONENT],
            "prices": workflow.price_rows(v), "status": st, "breakdown": v.cost_breakdown or {},
            "problems": (v.cost_breakdown or {}).get("problemas", []),
            "filaments": [f for f in (v.main_filament, v.secondary_filament) if f],
            "no_cost": [f for f in (v.main_filament, v.secondary_filament) if f and f.avg_cost_per_g <= 0],
        })

    last = product.variants.order_by("-created_at").first()
    vform = VariantCreateForm(initial={
        "size": "Único" if not variants else "",
        "main_filament": last.main_filament_id if last else None,
        "secondary_filament": last.secondary_filament_id if last else None,
    })
    photos = product.assets.filter(kind__in=[Asset.Kind.PHOTO, Asset.Kind.RENDER]).order_by("-is_main", "order", "id")

    # Assistente: mostra só a etapa escolhida; sem escolha, abre na próxima pendente.
    nxt = workflow.next_step(steps)
    keys = [s.key for s in steps]
    key = request.GET.get("etapa")
    if key not in keys:
        key = nxt.key if nxt else "publicar"
    i = keys.index(key)
    current = steps[i]
    return render(request, "catalog/product_detail.html", {
        **shell(request, product.name),
        "product": product, "steps": steps, "next": nxt, "step": current,
        "prev_step": steps[i - 1] if i > 0 else None, "next_in_order": steps[i + 1] if i + 1 < len(steps) else None,
        "data_form": ProductDataForm(instance=product) if key == "dados" else None,
        "parts": parts, "has_secondary": has_secondary, "library_parts": library_parts, "library_files": library_files, "new_part_form": PartForm(prefix="new"),
        "file_form": PartFileForm(), "variants": variants, "variant_form": vform, "photos": photos,
        "no_cost_filaments": FilamentSKU.objects.filter(active=True, avg_cost_per_g__lte=0).exists(),
        "has_filaments": FilamentSKU.objects.filter(active=True).exists(),
        "statuses": Product.Status.choices,
    })


@login_required
def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = ProductDataForm(request.POST or None, instance=product)
    if request.method == "POST" and form.is_valid():
        form.save()
        if {"license_cost_per_unit"} & set(form.changed_data):
            workflow.refresh_product(product)
        messages.success(request, "Dados do produto salvos.")
        return redirect(_safe_next(request, _ficha(product.pk, "dados")))
    return render(request, "catalog/product_edit.html", {**shell(request, f"Dados · {product.name}"), "form": form, "product": product})


@login_required
@require_POST
def product_status(request, pk):
    product = get_object_or_404(Product, pk=pk)
    status = request.POST.get("status")
    if status not in Product.Status.values:
        return redirect(_ficha(pk))
    if status in (Product.Status.ACTIVE, Product.Status.SEASONAL) and not product.ready_for_catalog:
        messages.warning(request, "Ativado, mas ainda não aparece no catálogo e na loja: conclua as etapas pendentes.")
    product.status = status
    product.save(update_fields=["status", "updated_at"])
    messages.success(request, f"Status: {product.get_status_display()}.")
    return redirect(_ficha(pk, "publicar"))


# --- peças e arquivos ------------------------------------------------------

@login_required
@require_POST
def part_add(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = PartForm(request.POST, prefix="new")
    if form.is_valid():
        part = form.save(commit=False)
        part.product = product
        part.save()
        workflow.sync_bom_with_parts(product)
        workflow.refresh_product(product)
        messages.success(request, f"Peça “{part.name}” criada. Suba o 3MF fatiado dela.")
    else:
        _form_errors(request, form)
    return redirect(_ficha(pk, "pecas"))


@login_required
@require_POST
def part_update(request, pk):
    part = get_object_or_404(Part, pk=pk)
    # cada formulário da tela mostra só alguns campos: altera apenas os que vieram no envio
    sent = [f for f in PartForm.Meta.fields if f"p{part.pk}-{f}" in request.POST]
    form = forms.modelform_factory(Part, form=PartForm, fields=sent or PartForm.Meta.fields)(
        request.POST, instance=part, prefix=f"p{part.pk}")
    if form.is_valid():
        form.save()
        if "default_qty" in form.changed_data:
            part.bom_items.update(quantity=part.default_qty)
        if "only_sizes" in form.changed_data:
            workflow.sync_bom_with_parts(part.product)
        workflow.refresh_product(part.product)
        messages.success(request, f"Peça “{part.name}” salva.")
    else:
        _form_errors(request, form)
    return redirect(_safe_next(request, _ficha(part.product_id, "pecas")))


@login_required
@require_POST
def part_delete(request, pk):
    part = get_object_or_404(Part, pk=pk)
    product = part.product
    name = part.name
    others = list(part.shared_in.all())
    if others:
        messages.error(request, f"A peça “{name}” também é usada em {', '.join(o.name for o in others)}. Desvincule lá antes de remover.")
        return redirect(_ficha(product.pk, "pecas"))
    with transaction.atomic():
        part.bom_items.all().delete()
        try:
            part.delete()
        except ProtectedError:
            transaction.set_rollback(True)
            messages.error(request, f"A peça “{name}” já tem impressões registradas e não pode ser removida.")
            return redirect(_ficha(product.pk, "pecas"))
    workflow.refresh_product(product)
    messages.success(request, f"Peça “{name}” removida.")
    return redirect(_ficha(product.pk, "pecas"))


@login_required
@require_POST
def part_reuse(request, pk):
    """Usa uma peça já cadastrada em outro produto: compartilhando (mesmo 3MF) ou copiando."""
    product = get_object_or_404(Product, pk=pk)
    src = get_object_or_404(Part.objects.select_related("product"), pk=request.POST.get("part"))
    if request.POST.get("mode") == "copy":
        new = Part.objects.create(
            product=product, name=src.name, default_qty=src.default_qty, color_slot=src.color_slot,
            waste_pct_override=src.waste_pct_override, failure_pct_override=src.failure_pct_override, notes=src.notes,
        )
        for a in src.assets.filter(kind__in=MODEL_KINDS):
            Asset.objects.create(
                product=product, part=new, kind=a.kind, file=a.file.name, name=a.name, version=a.version, sha256=a.sha256,
                size_bytes=a.size_bytes, printer=a.printer, is_production=a.is_production, thumbnail=a.thumbnail.name,
                grams_total=a.grams_total, print_time_min=a.print_time_min, meta=a.meta, parsed_at=a.parsed_at or timezone.now(),
                parse_error=a.parse_error, uploaded_by=request.user,
            )
        msg = f"Peça “{src.name}” copiada de {src.product.name}, já com o 3MF. É independente: mudanças aqui não afetam o original."
    else:
        product.shared_parts.add(src)
        msg = f"Peça “{src.name}” de {src.product.name} agora também é usada aqui. Trocar o 3MF dela vale para os dois produtos."
    _drop_unused_placeholder(product)
    workflow.sync_bom_with_parts(product)
    workflow.refresh_product(product)
    messages.success(request, msg)
    return redirect(_ficha(product.pk, "pecas"))


@login_required
@require_POST
def part_from_file(request, pk):
    """Transforma um 3MF fatiado solto da biblioteca em peça deste produto."""
    product = get_object_or_404(Product, pk=pk)
    asset = get_object_or_404(Asset, pk=request.POST.get("asset"), kind=Asset.Kind.THREEMF_SLICED, part__isnull=True)
    name = request.POST.get("name") or asset.name.split(".")[0].replace("_", " ").strip().capitalize() or "Peça"
    part = Part.objects.create(product=product, name=name[:80])
    asset.part, asset.product, asset.is_production = part, product, True
    asset.save(update_fields=["part", "product", "is_production"])
    _drop_unused_placeholder(product)
    workflow.sync_bom_with_parts(product)
    workflow.refresh_product(product)
    messages.success(request, f"Peça “{part.name}” criada com o 3MF {asset.name} ({asset.grams_total} g, {asset.print_time_min} min).")
    return redirect(_ficha(product.pk, "pecas"))


@login_required
@require_POST
def part_unlink(request, pk, part_pk):
    """Tira uma peça compartilhada deste produto (ela continua no produto de origem)."""
    product = get_object_or_404(Product, pk=pk)
    part = get_object_or_404(Part, pk=part_pk)
    product.shared_parts.remove(part)
    workflow.sync_bom_with_parts(product)
    workflow.refresh_product(product)
    messages.success(request, f"Peça “{part.name}” desvinculada deste produto.")
    return redirect(_ficha(product.pk, "pecas"))


def _drop_unused_placeholder(product: Product) -> None:
    """A 'Peça única' criada junto com o produto sai se ficou vazia e outra peça entrou no lugar."""
    for p in product.parts.filter(name="Peça única"):
        if not p.assets.exists() and not p.jobs.exists() and not p.shared_in.exists():
            p.bom_items.all().delete()
            p.delete()


@login_required
@require_POST
def part_upload(request, pk):
    part = get_object_or_404(Part.objects.select_related("product"), pk=pk)
    form = PartFileForm(request.POST, request.FILES)
    if not form.is_valid():
        _form_errors(request, form)
        return redirect(_safe_next(request, _ficha(part.product_id, "pecas")))
    printer = form.cleaned_data["printer"]
    # O arquivo novo vira o de produção desta peça para a mesma impressora; os anteriores ficam como histórico.
    old = part.assets.filter(kind=Asset.Kind.THREEMF_SLICED, is_production=True, printer=printer)
    version = (part.assets.filter(kind=Asset.Kind.THREEMF_SLICED).order_by("-version").values_list("version", flat=True).first() or 0) + 1
    old.update(is_production=False)
    asset = Asset.objects.create(
        product=part.product, part=part, kind=Asset.Kind.THREEMF_SLICED, file=form.cleaned_data["file"],
        printer=printer, is_production=True, version=version, uploaded_by=request.user,
    )
    asset.refresh_from_db()
    if asset.parse_error:
        asset.is_production = False
        asset.save(update_fields=["is_production"])
        part.assets.filter(pk__in=list(old.values_list("pk", flat=True))).update(is_production=True)
        messages.error(request, f"Não consegui ler o arquivo: {asset.parse_error}. Exporte pelo Bambu Studio em Arquivo → Exportar → arquivo fatiado da placa.")
    else:
        workflow.refresh_product(part.product)
        for other in part.shared_in.all():
            workflow.refresh_product(other)
        if asset.plates_pending:
            messages.info(request, f"O 3MF tem {len(asset.plates)} placas. Diga se são tamanhos diferentes ou partes do mesmo objeto.")
            return redirect("catalog:part_plates", pk=part.pk)
        messages.success(request, f"{part.name}: {asset.grams_total} g e {asset.print_time_min} min lidos do 3MF. Custo recalculado.")
    return redirect(_safe_next(request, _ficha(part.product_id, "pecas")))


def _refresh_plate_names(asset: Asset) -> Asset:
    """Arquivos lidos antes da versão que guarda nome e gramas por placa: relê uma vez."""
    if asset.plates and any("grams" not in p for p in asset.plates):
        parse_asset(asset)
        asset.refresh_from_db()
    return asset


@login_required
def part_plates(request, pk):
    part = get_object_or_404(Part.objects.select_related("product"), pk=pk)
    product = part.product
    asset = part.production_assets().first()
    if asset is None or len(asset.plates) < 2:
        messages.info(request, "Esta peça não tem 3MF com mais de uma placa.")
        return redirect(_ficha(product.pk, "pecas"))
    asset = _refresh_plate_names(asset)
    has_variant = product.variants.exists()
    filaments = FilamentSKU.objects.filter(active=True).order_by("brand", "material", "color_name")
    if request.method == "POST":
        mode = request.POST.get("mode")
        labels = [request.POST.get(f"label_{p['index']}", "") for p in asset.plates]
        fil = filaments.filter(pk=request.POST.get("filament") or 0).first()
        try:
            log = workflow.apply_plates(part, mode, labels, fil)
        except workflow.PlatesError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, " · ".join(log[:6]) + (" …" if len(log) > 6 else ""))
            return redirect(_ficha(product.pk, "variantes" if mode == Asset.PLATES_SIZES else "pecas"))
    plates = [{
        "index": p["index"], "label": Asset.plate_label(p), "grams": Asset.plate_grams(p),
        "minutes": round(int(p.get("time_s") or 0) / 60), "objects": [o for o in p.get("objects", []) if o],
    } for p in asset.plates]
    return render(request, "catalog/part_plates.html", {
        **shell(request, f"Placas · {part.name}"), "part": part, "product": product, "asset": asset, "plates": plates,
        "has_variant": has_variant, "filaments": filaments, "decided": asset.plates_mode,
    })


@login_required
@require_POST
def asset_delete(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    product_id = asset.product_id or (asset.part.product_id if asset.part_id else None)
    was_production = asset.is_production
    part = asset.part
    asset.delete()
    if was_production and part:
        # promove o 3MF mais recente que sobrou
        nxt = part.assets.filter(kind=Asset.Kind.THREEMF_SLICED, parse_error="").order_by("-version").first()
        if nxt:
            nxt.is_production = True
            nxt.save(update_fields=["is_production"])
    if product_id:
        workflow.refresh_product(Product.objects.get(pk=product_id))
    messages.success(request, "Arquivo removido.")
    anchor = "fotos" if asset.kind in (Asset.Kind.PHOTO, Asset.Kind.RENDER) else "pecas"
    return redirect(_ficha(product_id, anchor) if product_id else reverse("catalog:product_list"))


# --- variantes ---------------------------------------------------------------

@login_required
@require_POST
def variant_add(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = VariantCreateForm(request.POST)
    if not form.is_valid():
        _form_errors(request, form)
        return redirect(_ficha(pk, "variantes"))
    d = form.cleaned_data
    variant = Variant.objects.create(
        product=product, sku=workflow.suggest_variant_sku(product, d["size"], d["primary_color"]), size=d["size"],
        primary_color=d["primary_color"], secondary_color=d["secondary_color"],
        main_filament=d["main_filament"], secondary_filament=d["secondary_filament"],
        is_default=not product.variants.exists(),
    )
    workflow.refresh_variant(variant)
    messages.success(request, f"Variante {variant.sku} criada com custo R$ {variant.cost_cached}. Confira e aprove o preço.")
    return redirect(_ficha(pk, "variantes", f"v{variant.pk}"))


@login_required
def variant_edit(request, pk):
    variant = get_object_or_404(Variant.objects.select_related("product"), pk=pk)
    product = variant.product
    form = VariantForm(request.POST or None, instance=variant)
    comp_form = ComponentLineForm()
    if request.method == "POST":
        action = request.POST.get("action", "save")
        if action == "add_component":
            comp_form = ComponentLineForm(request.POST)
            if comp_form.is_valid():
                BOMItem.objects.create(variant=variant, kind=BOMItem.Kind.COMPONENT, **comp_form.cleaned_data)
                workflow.refresh_variant(variant)
                messages.success(request, "Componente adicionado e custo recalculado.")
                return redirect("catalog:variant_edit", pk=pk)
            form = VariantForm(instance=variant)
        elif action.startswith("remove_item:"):
            BOMItem.objects.filter(variant=variant, pk=action.split(":", 1)[1], kind=BOMItem.Kind.COMPONENT).delete()
            workflow.refresh_variant(variant)
            messages.success(request, "Componente removido.")
            return redirect("catalog:variant_edit", pk=pk)
        elif form.is_valid():
            form.save()
            for item in variant.bom_items.filter(kind=BOMItem.Kind.PART):
                raw = request.POST.get(f"qty_{item.pk}")
                if raw:
                    try:
                        item.quantity = Decimal(raw.replace(",", "."))
                        item.save(update_fields=["quantity"])
                    except InvalidOperation:
                        pass
            if form.cleaned_data.get("is_default"):
                product.variants.exclude(pk=variant.pk).update(is_default=False)
            workflow.refresh_variant(variant)
            messages.success(request, f"{variant.sku} salva. Custo R$ {variant.cost_cached}.")
            return redirect(_ficha(product.pk, "variantes", f"v{variant.pk}"))
    return render(request, "catalog/variant_edit.html", {
        **shell(request, f"Variante {variant.sku}"),
        "variant": variant, "product": product, "form": form, "comp_form": comp_form,
        "parts": variant.bom_items.filter(kind=BOMItem.Kind.PART).select_related("part"),
        "components": variant.bom_items.filter(kind=BOMItem.Kind.COMPONENT).select_related("component"),
        "has_secondary": product.parts.filter(color_slot=Part.ColorSlot.SECONDARY).exists(),
    })


@login_required
@require_POST
def variant_delete(request, pk):
    variant = get_object_or_404(Variant, pk=pk)
    product, sku, was_default = variant.product, variant.sku, variant.is_default
    n_orders = variant.orders.count()
    if n_orders:
        messages.error(request, f"{sku} tem {n_orders} ordem(ns) de produção registrada(s) e não pode ser excluída (o histórico precisa dela).")
        return redirect(_ficha(product.pk, "variantes", f"v{variant.pk}"))
    try:
        with transaction.atomic():
            variant.delete()
    except ProtectedError:
        messages.error(request, f"{sku} está em uso e não pode ser excluída.")
        return redirect(_ficha(product.pk, "variantes"))
    if was_default:
        other = product.variants.order_by("created_at").first()
        if other:
            other.is_default = True
            other.save(update_fields=["is_default"])
    messages.success(request, f"Variante {sku} excluída.")
    return redirect(_ficha(product.pk, "variantes"))


@login_required
@require_POST
def variant_recalculate(request, pk):
    variant = get_object_or_404(Variant, pk=pk)
    workflow.refresh_variant(variant)
    messages.success(request, f"{variant.sku}: custo R$ {variant.cost_cached} e preços sugeridos recalculados.")
    return redirect(_safe_next(request, _ficha(variant.product_id, "variantes", f"v{variant.pk}")))


@login_required
@require_POST
def variant_prices(request, pk):
    """Aprova os preços de uma variante: todos de uma vez, ou só o canal do botão clicado."""
    variant = get_object_or_404(Variant, pk=pk)
    only = request.POST.get("only")
    approved, problems = [], []
    for row in latest_prices(variant):
        if only and str(row.channel_id) != only:
            continue
        raw = (request.POST.get(f"price_{row.channel_id}") or "").strip().replace("R$", "").replace(" ", "")
        if "," in raw:
            raw = raw.replace(".", "").replace(",", ".")
        try:
            price = Decimal(raw) if raw else row.current_price
        except InvalidOperation:
            problems.append(f"{row.channel}: valor inválido")
            continue
        if price <= 0:
            problems.append(f"{row.channel}: o preço precisa ser maior que zero")
            continue
        new = approve_price(row, request.user, price=price)
        approved.append(new)
    for p in problems:
        messages.error(request, p)
    if approved:
        below = [r for r in approved if r.below_minimum]
        msg = ", ".join(f"{r.channel} R$ {r.current_price}" for r in approved)
        messages.success(request, f"{variant.sku}: aprovado {msg}.")
        for r in below:
            messages.warning(request, f"{r.channel}: margem de {r.margin_pct}% está abaixo do mínimo do canal.")
    fallback = _ficha(variant.product_id, "preco", f"v{variant.pk}")
    if approved and not any(workflow.variant_price_status(x)["pending"] for x in variant.product.variants.all()):
        fallback = _ficha(variant.product_id, "fotos")  # todos os preços aprovados: segue para a próxima etapa
    return redirect(_safe_next(request, fallback))


# --- fotos -------------------------------------------------------------------

@login_required
@require_POST
def photo_upload(request, pk):
    product = get_object_or_404(Product, pk=pk)
    files = request.FILES.getlist("photos")
    ok_types = (".jpg", ".jpeg", ".png", ".webp")
    has_main = product.assets.filter(kind=Asset.Kind.PHOTO, is_main=True).exists()
    n = 0
    order = product.assets.filter(kind=Asset.Kind.PHOTO).count()
    for f in files:
        if not f.name.lower().endswith(ok_types):
            messages.error(request, f"{f.name}: envie JPG, PNG ou WEBP.")
            continue
        Asset.objects.create(
            product=product, kind=Asset.Kind.PHOTO, file=f, is_main=not has_main and n == 0, order=order + n,
            uploaded_by=request.user,
        )
        n += 1
    if n:
        messages.success(request, f"{n} foto{'s' if n > 1 else ''} adicionada{'s' if n > 1 else ''}.")
    return redirect(_ficha(pk, "fotos"))


@login_required
@require_POST
def photo_main(request, pk):
    asset = get_object_or_404(Asset, pk=pk, kind__in=[Asset.Kind.PHOTO, Asset.Kind.RENDER])
    Asset.objects.filter(product_id=asset.product_id, is_main=True).update(is_main=False)
    asset.is_main = True
    if asset.kind != Asset.Kind.PHOTO:
        asset.kind = Asset.Kind.PHOTO
    asset.save(update_fields=["is_main", "kind"])
    messages.success(request, "Foto principal definida.")
    return redirect(_ficha(asset.product_id, "fotos"))
