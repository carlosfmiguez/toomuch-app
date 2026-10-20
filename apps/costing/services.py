"""
Liga o motor de custo ao banco: monta o CostInput a partir da BOM de uma variante,
grava o custo na variante e gera os preços sugeridos por canal.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import BOMItem, Variant

from . import engine
from .models import Channel, ChannelFee, GlobalParams, PriceByChannel, Printer


@dataclass
class CostProblem:
    message: str


def build_cost_input(variant: Variant, printer: Printer | None = None, params: GlobalParams | None = None):
    """Retorna (CostInput, [problemas]). Problemas não impedem o cálculo, mas o deixam incompleto."""
    params = params or GlobalParams.current()
    problems: list[CostProblem] = []
    inp = engine.CostInput(
        labor_minutes=Decimal(variant.labor_minutes),
        labor_rate_per_hour=params.labor_cost_per_hour,
        packaging_cost=params.packaging_default_cost,
        license_cost=variant.product.license_cost_per_unit,
        waste_pct=params.waste_pct,
        failure_pct=params.failure_pct,
    )
    items = variant.bom_items.select_related("part", "component").all()
    if not items:
        problems.append(CostProblem("A variante não tem itens de composição (BOM)."))

    part_failure_pcts: list[Decimal] = []
    for item in items:
        qty = Decimal(item.quantity)
        if item.kind == BOMItem.Kind.COMPONENT:
            comp = item.component
            inp.components.append(engine.ComponentUse(qty, comp.unit_cost, comp.name, comp.is_packaging))
            continue

        part = item.part
        asset = part.production_asset(printer)
        if asset is None:
            problems.append(CostProblem(f"Peça '{part.name}' sem 3MF fatiado de produção."))
            continue
        filament = variant.filament_for_slot(item.effective_slot)
        if filament is None:
            problems.append(CostProblem(f"Peça '{part.name}': nenhum filamento mapeado para o slot."))
            continue
        if filament.avg_cost_per_g <= 0:
            problems.append(CostProblem(f"Filamento '{filament}' sem custo médio (registre uma compra)."))

        grams = Decimal(asset.grams_total or 0)
        if not grams:
            problems.append(CostProblem(f"3MF da peça '{part.name}' sem gramas lidas."))
        inp.filaments.append(engine.FilamentUse(
            grams=grams * qty, cost_per_g=filament.avg_cost_per_g, waste_pct=part.waste_pct_override,
            label=f"{part.name} ({filament})",
        ))

        machine = asset.printer or printer or Printer.objects.filter(status=Printer.Status.ACTIVE).first()
        hourly = machine.hourly_cost(params) if machine else Decimal("0")
        if machine is None:
            problems.append(CostProblem("Nenhuma impressora ativa cadastrada para a hora-máquina."))
        inp.machines.append(engine.MachineUse(asset.print_hours * qty, hourly, f"{part.name} em {machine or '—'}"))
        if part.failure_pct_override is not None:
            part_failure_pcts.append(part.failure_pct_override)

    if part_failure_pcts:
        # Regra simples do MVP: se alguma peça define taxa própria, usa a maior delas.
        inp.failure_pct = max(part_failure_pcts)
    return inp, problems


@transaction.atomic
def compute_variant_cost(variant: Variant, printer: Printer | None = None, params: GlobalParams | None = None):
    params = params or GlobalParams.current()
    inp, problems = build_cost_input(variant, printer, params)
    breakdown = engine.compute_cost(inp)
    variant.cost_cached = breakdown.total
    variant.cost_cached_at = timezone.now()
    data = breakdown.as_dict()
    data["problemas"] = [p.message for p in problems]
    data["parametros"] = {
        "energia_kwh": str(params.energy_tariff_kwh), "mao_de_obra_h": str(params.labor_cost_per_hour),
        "desperdicio_pct": str(params.waste_pct), "falha_pct": str(inp.failure_pct), "simples_pct": str(params.simples_pct),
    }
    variant.cost_breakdown = data
    variant.save(update_fields=["cost_cached", "cost_cached_at", "cost_breakdown"])
    return breakdown, problems


def terms_for(fee: ChannelFee, params: GlobalParams | None = None) -> engine.ChannelTerms:
    return engine.ChannelTerms(
        fee_pct=fee.fee_pct, fixed_fee=fee.fixed_fee, fixed_fee_below_price=fee.fixed_fee_below_price,
        shipping_subsidy=fee.shipping_subsidy, shipping_subsidy_from_price=fee.shipping_subsidy_from_price,
        tax_pct=fee.tax_pct(params), target_margin_pct=fee.target_margin_pct, min_margin_pct=fee.min_margin_pct,
        rounding=fee.rounding,
    )


@transaction.atomic
def compute_prices(variant: Variant, params: GlobalParams | None = None, keep_current: bool = True) -> list[PriceByChannel]:
    """Gera uma linha nova de preço por canal ativo. Mantém o preço vigente aprovado, se houver."""
    params = params or GlobalParams.current()
    if variant.cost_cached is None:
        compute_variant_cost(variant, params=params)
    cost = variant.cost_cached
    rows: list[PriceByChannel] = []
    for channel in Channel.objects.filter(status=Channel.Status.ACTIVE):
        fee = channel.current_fee()
        if fee is None:
            continue
        terms = terms_for(fee, params)
        result = engine.suggest_price(cost, terms)
        previous = variant.prices.filter(channel=channel).order_by("-created_at").first()
        current = result.suggested
        approved_by, approved_at = None, None
        if keep_current and previous and previous.approved:
            current = previous.current_price
            approved_by, approved_at = previous.approved_by, previous.approved_at
        margin = engine.margin_at(current, cost, terms)
        rows.append(PriceByChannel.objects.create(
            variant=variant, channel=channel, cost_at=cost, suggested_price=result.suggested, current_price=current,
            margin_pct=margin, below_minimum=margin < fee.min_margin_pct, approved_by=approved_by, approved_at=approved_at,
        ))
    return rows


def latest_prices(variant: Variant) -> list[PriceByChannel]:
    seen: set[int] = set()
    out: list[PriceByChannel] = []
    for row in variant.prices.select_related("channel").order_by("-created_at"):
        if row.channel_id in seen:
            continue
        seen.add(row.channel_id)
        out.append(row)
    out.sort(key=lambda r: (r.channel.order, r.channel.name))
    return out


@transaction.atomic
def approve_price(row: PriceByChannel, user, price: Decimal | None = None, note: str = "") -> PriceByChannel:
    """Aprova (ou sobrescreve) o preço vigente de uma variante em um canal, criando uma linha nova."""
    fee = row.channel.current_fee()
    terms = terms_for(fee) if fee else engine.ChannelTerms()
    current = Decimal(price) if price is not None else row.current_price
    margin = engine.margin_at(current, row.cost_at, terms)
    return PriceByChannel.objects.create(
        variant=row.variant, channel=row.channel, cost_at=row.cost_at, suggested_price=row.suggested_price,
        current_price=current, margin_pct=margin, below_minimum=bool(fee) and margin < fee.min_margin_pct,
        approved_by=user, approved_at=timezone.now(), note=note,
    )


def recompute_all(params: GlobalParams | None = None) -> int:
    """Recalcula custo e preços de todas as variantes de produtos não descontinuados."""
    n = 0
    for variant in Variant.objects.exclude(product__status="discontinued").select_related("product"):
        compute_variant_cost(variant, params=params)
        compute_prices(variant, params=params)
        n += 1
    return n
