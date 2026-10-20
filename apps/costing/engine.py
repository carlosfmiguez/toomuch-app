"""
Motor de custo e preço — funções puras, sem banco.

Fórmulas (seção "Regras de custo e precificação" da especificação):

    C_fil   = Σ g_i · (1 + d) · c_i
    c_h     = P / V + m + (W / 1000) · t            (duas casas)
    C_maq   = Σ h_j · c_h,j
    C_mdo   = (min_pos + min_mont + min_emb) / 60 · c_mdo
    C_total = C_fil + C_maq + C_mdo + C_comp + C_emb + C_lic + f · (C_fil + C_maq)
    Preço   = (C_total + F_fixo + F_frete) / (1 − τ_canal − τ_imp − M)
    c_novo  = (S · c_atual + G · c_compra) / (S + G)

Tudo em Decimal; o arredondamento a centavos acontece só na apresentação de
cada parcela e no total.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

D = Decimal
TWO = D("0.01")
HUNDRED = D("100")


def q2(value: Decimal) -> Decimal:
    return D(value).quantize(TWO, ROUND_HALF_UP)


def pct(value) -> Decimal:
    """4 → 0.04"""
    return D(value) / HUNDRED


# ---------------------------------------------------------------------------
# Custo
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilamentUse:
    grams: Decimal
    cost_per_g: Decimal
    waste_pct: Decimal | None = None  # vazio usa o global
    label: str = ""


@dataclass(frozen=True)
class MachineUse:
    hours: Decimal
    hourly_cost: Decimal
    label: str = ""


@dataclass(frozen=True)
class ComponentUse:
    quantity: Decimal
    unit_cost: Decimal
    label: str = ""
    is_packaging: bool = False


@dataclass
class CostInput:
    filaments: list[FilamentUse] = field(default_factory=list)
    machines: list[MachineUse] = field(default_factory=list)
    components: list[ComponentUse] = field(default_factory=list)
    labor_minutes: Decimal = D("0")
    labor_rate_per_hour: Decimal = D("0")
    packaging_cost: Decimal = D("0")  # além dos componentes marcados como embalagem
    license_cost: Decimal = D("0")
    waste_pct: Decimal = D("2")  # global, em %
    failure_pct: Decimal = D("5")  # global, em %


@dataclass(frozen=True)
class CostBreakdown:
    filament: Decimal
    machine: Decimal
    labor: Decimal
    components: Decimal
    packaging: Decimal
    license: Decimal
    failure: Decimal
    total: Decimal
    lines: list[dict]

    def as_dict(self) -> dict:
        return {
            "filamento": str(q2(self.filament)),
            "hora_maquina": str(q2(self.machine)),
            "mao_de_obra": str(q2(self.labor)),
            "componentes": str(q2(self.components)),
            "embalagem": str(q2(self.packaging)),
            "licenca": str(q2(self.license)),
            "falha": str(q2(self.failure)),
            "total": str(q2(self.total)),
            "linhas": self.lines,
        }


def hourly_cost(price, lifetime_hours, maintenance_per_hour, power_w, tariff_kwh) -> Decimal:
    """Custo da hora-máquina, com duas casas (é assim que aparece nas telas e no cálculo)."""
    price, maintenance, tariff = D(price), D(maintenance_per_hour), D(tariff_kwh)
    lifetime = D(lifetime_hours) if lifetime_hours else D(1)
    energy = D(power_w) / D(1000) * tariff
    return q2(price / lifetime + maintenance + energy)


def compute_cost(inp: CostInput) -> CostBreakdown:
    lines: list[dict] = []

    filament = D("0")
    for f in inp.filaments:
        waste = pct(inp.waste_pct if f.waste_pct is None else f.waste_pct)
        value = D(f.grams) * (1 + waste) * D(f.cost_per_g)
        filament += value
        lines.append({"tipo": "filamento", "rotulo": f.label, "gramas": str(f.grams), "valor": str(q2(value))})

    machine = D("0")
    for m in inp.machines:
        value = D(m.hours) * D(m.hourly_cost)
        machine += value
        lines.append({"tipo": "maquina", "rotulo": m.label, "horas": str(m.hours), "valor": str(q2(value))})

    labor = D(inp.labor_minutes) / D(60) * D(inp.labor_rate_per_hour)
    if labor:
        lines.append({"tipo": "mao_de_obra", "rotulo": f"{inp.labor_minutes} min", "valor": str(q2(labor))})

    components = D("0")
    packaging = D(inp.packaging_cost)
    for c in inp.components:
        value = D(c.quantity) * D(c.unit_cost)
        if c.is_packaging:
            packaging += value
        else:
            components += value
        lines.append({"tipo": "embalagem" if c.is_packaging else "componente", "rotulo": c.label, "valor": str(q2(value))})

    license_cost = D(inp.license_cost)
    failure = pct(inp.failure_pct) * (filament + machine)
    total = filament + machine + labor + components + packaging + license_cost + failure

    return CostBreakdown(
        filament=filament, machine=machine, labor=labor, components=components, packaging=packaging,
        license=license_cost, failure=failure, total=q2(total), lines=lines,
    )


def weighted_average_cost(stock_g, current_cost_per_g, purchase_g, purchase_cost_per_g) -> Decimal:
    stock_g, purchase_g = D(stock_g), D(purchase_g)
    total = stock_g + purchase_g
    if total <= 0:
        return D(purchase_cost_per_g)
    return (stock_g * D(current_cost_per_g) + purchase_g * D(purchase_cost_per_g)) / total


# ---------------------------------------------------------------------------
# Preço
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChannelTerms:
    fee_pct: Decimal = D("0")
    fixed_fee: Decimal = D("0")
    fixed_fee_below_price: Decimal | None = None
    shipping_subsidy: Decimal = D("0")
    shipping_subsidy_from_price: Decimal | None = None
    tax_pct: Decimal = D("0")
    target_margin_pct: Decimal = D("30")
    min_margin_pct: Decimal = D("20")
    rounding: str = "ceil_90"  # ou "cents"


@dataclass(frozen=True)
class PriceResult:
    suggested: Decimal
    raw: Decimal
    fixed_fee_applied: Decimal
    shipping_applied: Decimal
    margin_pct: Decimal


def round_up_90(value: Decimal) -> Decimal:
    """Para cima até o próximo valor terminado em ,90: 118,34 → 118,90; 118,95 → 119,90."""
    value = D(value)
    whole = value.to_integral_value(rounding=ROUND_FLOOR)
    candidate = whole + D("0.90")
    if candidate < value:
        candidate += 1
    return candidate.quantize(TWO)


def _fixed_fee_for(price: Decimal, terms: ChannelTerms) -> Decimal:
    if terms.fixed_fee_below_price is not None and price >= terms.fixed_fee_below_price:
        return D("0")
    return D(terms.fixed_fee)


def _shipping_for(price: Decimal, terms: ChannelTerms) -> Decimal:
    if terms.shipping_subsidy_from_price is not None and price < terms.shipping_subsidy_from_price:
        return D("0")
    return D(terms.shipping_subsidy)


def margin_at(price, cost, terms: ChannelTerms) -> Decimal:
    """Margem líquida (%) sobre o preço, depois de taxa, imposto, fixo e frete subsidiado."""
    price, cost = D(price), D(cost)
    if price <= 0:
        return D("0")
    net = price * (1 - pct(terms.fee_pct) - pct(terms.tax_pct)) - _fixed_fee_for(price, terms) - _shipping_for(price, terms) - cost
    return (net / price * HUNDRED).quantize(TWO, ROUND_HALF_UP)


def suggest_price(cost, terms: ChannelTerms) -> PriceResult:
    cost = D(cost)
    denominator = 1 - pct(terms.fee_pct) - pct(terms.tax_pct) - pct(terms.target_margin_pct)
    if denominator <= 0:
        raise ValueError("Taxa + imposto + margem-alvo chegam a 100%: não há preço possível.")

    # As parcelas fixas podem depender do preço (faixas); iteramos até estabilizar.
    fixed, shipping = D(terms.fixed_fee), D(terms.shipping_subsidy)
    raw = (cost + fixed + shipping) / denominator
    for _ in range(4):
        fixed, shipping = _fixed_fee_for(raw, terms), _shipping_for(raw, terms)
        new_raw = (cost + fixed + shipping) / denominator
        if q2(new_raw) == q2(raw):
            raw = new_raw
            break
        raw = new_raw

    suggested = round_up_90(raw) if terms.rounding == "ceil_90" else q2(raw)
    return PriceResult(
        suggested=suggested, raw=q2(raw),
        fixed_fee_applied=_fixed_fee_for(suggested, terms), shipping_applied=_shipping_for(suggested, terms),
        margin_pct=margin_at(suggested, cost, terms),
    )
