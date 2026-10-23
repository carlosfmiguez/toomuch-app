"""
O caminho de um produto, do rascunho ao catálogo, em etapas.

A ficha do produto mostra estas etapas em ordem; cada uma diz se está feita,
o que falta e onde resolver. Também concentra o recálculo automático de custo
e preço (sempre que algo que muda o custo é salvo) e os helpers de SKU.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .models import Asset, Part, Product, Variant


@dataclass
class Step:
    key: str
    number: int
    title: str
    done: bool
    todo: list[str] = field(default_factory=list)
    anchor: str = ""

    @property
    def state(self) -> str:
        return "done" if self.done else "todo"


def variant_price_status(variant: Variant) -> dict:
    """Situação dos preços vigentes de uma variante: aprovados, pendentes, abaixo da margem."""
    from apps.costing.services import latest_prices

    rows = latest_prices(variant)
    pending = [r for r in rows if not r.approved]
    below = [r for r in rows if r.below_minimum]
    return {"rows": rows, "pending": pending, "below": below, "all_approved": bool(rows) and not pending}


def steps_for(product: Product) -> list[Step]:
    parts = product.all_parts()
    variants = list(product.variants.all())

    # 1. Dados
    todo = []
    if not product.short_description:
        todo.append("Escreva a descrição curta (vai para o catálogo e a loja).")
    if not (product.height_mm and product.width_mm):
        todo.append("Informe as medidas.")
    if not product.license_is_valid:
        todo.append("Licença de venda inválida ou vencida.")
    s1 = Step("dados", 1, "Dados do produto", done=product.license_is_valid and bool(product.short_description), todo=todo, anchor="dados")

    # 2. Peças e 3MF
    todo = []
    if not parts:
        todo.append("Cadastre ao menos uma peça impressa.")
    for p in parts:
        prod_asset = p.production_assets().first()
        if prod_asset is not None and prod_asset.plates_pending:
            labels = ", ".join(Asset.plate_label(pl) for pl in prod_asset.plates)
            todo.append(f"O 3MF da peça “{p.name}” tem {len(prod_asset.plates)} placas ({labels}): "
                        "diga se são tamanhos diferentes ou partes do mesmo objeto.")
        if not p.production_assets().exists():
            if p.has_estimate:
                todo.append(f"Suba o 3MF fatiado da peça “{p.name}” (o custo usa a estimativa de {p.est_label}).")
            else:
                todo.append(f"Suba o 3MF fatiado da peça “{p.name}”.")
    s2 = Step("pecas", 2, "Peças e 3MF fatiado", done=bool(parts) and not todo, todo=todo, anchor="pecas")

    # 3. Variantes
    todo = []
    if not variants:
        todo.append("Crie a primeira variante (tamanho e cor).")
    has_secondary = any(p.color_slot == Part.ColorSlot.SECONDARY for p in parts)
    for v in variants:
        if not v.main_filament_id:
            todo.append(f"{v.sku}: escolha o filamento da cor principal.")
        if has_secondary and not v.secondary_filament_id:
            todo.append(f"{v.sku}: escolha o filamento da cor secundária.")
        for msg in (v.cost_breakdown or {}).get("problemas", []):
            if "filamento" in msg.lower() and "custo médio" in msg.lower():
                todo.append(f"{v.sku}: {msg}")
    s3 = Step("variantes", 3, "Variantes e custo", done=bool(variants) and not todo, todo=todo, anchor="variantes")

    # 4. Preço
    todo = []
    for v in variants:
        st = variant_price_status(v)
        if not st["rows"]:
            todo.append(f"{v.sku}: sem preço calculado.")
        elif st["pending"]:
            todo.append(f"{v.sku}: aprove o preço em {', '.join(r.channel.name for r in st['pending'])}.")
    s4 = Step("preco", 4, "Aprovar preço", done=bool(variants) and not todo, todo=todo, anchor="variantes")

    # 5. Fotos
    has_photo = product.assets.filter(kind=Asset.Kind.PHOTO).exists()
    s5 = Step("fotos", 5, "Fotos", done=has_photo, todo=[] if has_photo else ["Suba pelo menos uma foto."], anchor="fotos")

    # 6. Publicar
    ready = all(s.done for s in (s2, s4, s5)) and product.license_is_valid and all(v.bom_items.exists() for v in variants)
    active = product.status in (Product.Status.ACTIVE, Product.Status.SEASONAL)
    todo = []
    if not ready:
        todo.append("Conclua as etapas anteriores.")
    elif not active:
        todo.append("Tudo pronto: ative o produto para entrar no catálogo e na loja.")
    s6 = Step("publicar", 6, "Publicar", done=ready and active, todo=todo, anchor="publicar")
    return [s1, s2, s3, s4, s5, s6]


def next_step(steps: list[Step]) -> Step | None:
    # A etapa 1 não bloqueia: só sugere depois das obrigatórias.
    for s in steps[1:]:
        if not s.done:
            return s
    return None if steps[0].done else steps[0]


# --- custo e preço sempre atualizados ------------------------------------

def refresh_variant(variant: Variant) -> None:
    from apps.costing.services import compute_prices, compute_variant_cost

    variant.ensure_default_bom()
    compute_variant_cost(variant)
    compute_prices(variant)


def refresh_product(product: Product) -> None:
    for v in product.variants.all():
        refresh_variant(v)


def sync_bom_with_parts(product: Product) -> None:
    """Peça nova (própria ou compartilhada) entra na composição de todas as variantes; peça desvinculada sai."""
    from .models import BOMItem

    parts = product.all_parts()
    for v in product.variants.all():
        have = set(v.bom_items.filter(kind=BOMItem.Kind.PART).values_list("part_id", flat=True))
        fitting = [p for p in parts if p.fits(v)]
        for part in [p for p in fitting if p.pk not in have]:
            BOMItem.objects.create(variant=v, kind=BOMItem.Kind.PART, part=part, quantity=part.default_qty)
        allowed = {p.pk for p in fitting}
        v.bom_items.filter(kind=BOMItem.Kind.PART).exclude(part_id__in=allowed).delete()


# --- SKU -------------------------------------------------------------------

def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()


def size_code(size: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]", "", _ascii(size)).upper()
    if not s:
        return "U"
    if s in ("UNICO", "UNICA"):
        return "U"
    return s[:3] if len(s) > 3 and not s.isdigit() else s


def suggest_variant_sku(product: Product, size: str, color=None, exclude_pk: int | None = None) -> str:
    bits = [product.sku_base, size_code(size)]
    if color is not None:
        bits.append(color.code)
    base = "-".join(bits).upper()
    qs = Variant.objects.all()
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    taken = set(qs.filter(sku__startswith=base).values_list("sku", flat=True))
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def price_rows(variant: Variant) -> list[dict]:
    """Linhas de preço vigentes com as condições do canal (para a margem ao vivo na tela)."""
    from decimal import Decimal

    from apps.costing.services import latest_prices, terms_for

    out = []
    for row in latest_prices(variant):
        fee = row.channel.current_fee()
        out.append({"row": row, "terms": terms_for(fee) if fee else None, "min_margin": fee.min_margin_pct if fee else Decimal("0")})
    return out


def pending_approvals() -> list[dict]:
    """Variantes de produtos não descontinuados com algum preço vigente ainda não aprovado."""
    out = []
    qs = Variant.objects.exclude(product__status=Product.Status.DISCONTINUED).select_related("product", "primary_color")
    for v in qs:
        st = variant_price_status(v)
        if st["pending"]:
            out.append({"variant": v, **st, "price_rows": price_rows(v)})
    return out


# --- 3MF com várias placas -------------------------------------------------

class PlatesError(ValueError):
    pass


def plates_to_decide(product: Product) -> list[tuple[Part, Asset]]:
    out = []
    for part in product.all_parts():
        asset = part.production_assets().first()
        if asset is not None and asset.plates_pending:
            out.append((part, asset))
    return out


def apply_plates(part: Part, mode: str, labels: list[str] | None = None, filament=None) -> list[str]:
    """
    Resolve um 3MF de produção com várias placas.

    - parts: as placas são partes do mesmo objeto → a peça continua uma só e o custo soma as placas.
    - sizes: cada placa é um tamanho → a peça fica com a 1ª placa e ganha uma irmã por placa (mesmo arquivo,
      'placa do 3MF' e 'só nos tamanhos' próprios); cada tamanho vira variante (copiando filamentos da
      variante padrão, ou com o filamento informado se o produto ainda não tem variante).
    Retorna um resumo do que foi feito.
    """
    from django.db import transaction

    asset = part.production_assets().first()
    if asset is None or len(asset.plates) < 2:
        raise PlatesError("Esta peça não tem um 3MF de produção com mais de uma placa.")
    product = part.product
    log: list[str] = []
    if mode == Asset.PLATES_PARTS:
        asset.meta = {**asset.meta, "plates_mode": Asset.PLATES_PARTS}
        asset.save(update_fields=["meta"])
        part.plate_index = None
        part.save(update_fields=["plate_index"])
        refresh_product(product)
        return [f"{len(asset.plates)} placas somadas no custo da peça “{part.name}”."]

    if mode != Asset.PLATES_SIZES:
        raise PlatesError("Modo inválido.")
    plates = asset.plates
    labels = [(x or "").strip() for x in (labels or [])] or [Asset.plate_label(p) for p in plates]
    if len(labels) != len(plates) or any(not x for x in labels):
        raise PlatesError("Informe um tamanho para cada placa.")
    if len({x.upper() for x in labels}) != len(labels):
        raise PlatesError("Cada placa precisa de um tamanho diferente.")
    template = product.variants.filter(is_default=True).first() or product.variants.order_by("pk").first()
    if template is None and filament is None:
        raise PlatesError("O produto ainda não tem variante: escolha o filamento para criar as variantes de cada tamanho.")

    with transaction.atomic():
        base = product.name if part.name in ("Peça única", "") else part.name
        mode_meta = {**asset.meta, "plates_mode": Asset.PLATES_SIZES}
        asset.meta = mode_meta
        asset.save(update_fields=["meta"])
        part.plate_index = int(plates[0]["index"])
        part.only_sizes = labels[0]
        part.name = f"{base} {labels[0]}"[:80]
        part.save()
        log.append(f"peça “{part.name}” usa a placa {Asset.plate_label(plates[0])}")
        for plate, label in zip(plates[1:], labels[1:]):
            sib = Part.objects.create(
                product=product, name=f"{base} {label}"[:80], default_qty=part.default_qty, color_slot=part.color_slot,
                waste_pct_override=part.waste_pct_override, failure_pct_override=part.failure_pct_override,
                plate_index=int(plate["index"]), only_sizes=label,
            )
            Asset.objects.create(
                product=product, part=sib, kind=asset.kind, file=asset.file.name, name=asset.name, version=asset.version,
                sha256=asset.sha256, size_bytes=asset.size_bytes, printer=asset.printer, is_production=True,
                thumbnail=asset.thumbnail.name if asset.thumbnail else "", grams_total=asset.grams_total,
                print_time_min=asset.print_time_min, meta=mode_meta, parsed_at=asset.parsed_at, uploaded_by=asset.uploaded_by,
            )
            log.append(f"peça “{sib.name}” criada com a placa {Asset.plate_label(plate)}")

        # variantes: a 'Único' (sem pedido) vira o 1º tamanho; os demais são criados
        existing = {v.size.strip().upper(): v for v in product.variants.all()}
        unico = existing.get("ÚNICO") or existing.get("UNICO")
        if unico is not None and labels[0].upper() not in existing and not unico.orders.exists():
            unico.size = labels[0]
            unico.sku = suggest_variant_sku(product, labels[0], unico.primary_color, exclude_pk=unico.pk)
            unico.save(update_fields=["size", "sku"])
            existing = {v.size.strip().upper(): v for v in product.variants.all()}
            log.append(f"variante única virou o tamanho {labels[0]} ({unico.sku})")
        for label in labels:
            if label.upper() in existing:
                continue
            src = template
            v = Variant.objects.create(
                product=product, size=label,
                sku=suggest_variant_sku(product, label, src.primary_color if src else None),
                primary_color=src.primary_color if src else None, secondary_color=src.secondary_color if src else None,
                main_filament=src.main_filament if src else filament, secondary_filament=src.secondary_filament if src else None,
                minutes_post=src.minutes_post if src else 10, minutes_assembly=src.minutes_assembly if src else 0,
                minutes_packaging=src.minutes_packaging if src else 5,
                is_default=not product.variants.exists(),
            )
            if src is not None:  # componentes comprados (LED, caixa...) vão junto
                for item in src.bom_items.filter(kind="component"):
                    v.bom_items.create(kind=item.kind, component=item.component, quantity=item.quantity)
            log.append(f"variante {v.sku} criada")
        sync_bom_with_parts(product)
        refresh_product(product)
    return log
