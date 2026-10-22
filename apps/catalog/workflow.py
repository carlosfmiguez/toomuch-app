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
        if not p.production_assets().exists():
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

    for v in product.variants.all():
        have = set(v.bom_items.filter(kind=BOMItem.Kind.PART).values_list("part_id", flat=True))
        for part in [p for p in product.all_parts() if p.pk not in have]:
            BOMItem.objects.create(variant=v, kind=BOMItem.Kind.PART, part=part, quantity=part.default_qty)
        allowed = {p.pk for p in product.all_parts()}
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
