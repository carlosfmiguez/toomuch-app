"""
Custo e preço: impressoras, parâmetros globais com vigência, canais de venda com
taxas versionadas e o histórico de preço por variante e canal.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

TWO = Decimal("0.01")


class GlobalParams(models.Model):
    """Uma linha por vigência; a vigente é a mais recente com data <= hoje."""

    effective_from = models.DateField("vigente a partir de", default=timezone.localdate)
    energy_tariff_kwh = models.DecimalField("tarifa de energia (R$/kWh)", max_digits=8, decimal_places=4, default=Decimal("0.95"))
    labor_cost_per_hour = models.DecimalField("custo/hora de mão de obra (R$)", max_digits=8, decimal_places=2, default=Decimal("30.00"))
    waste_pct = models.DecimalField("desperdício de filamento (%)", max_digits=5, decimal_places=2, default=Decimal("2.00"))
    failure_pct = models.DecimalField("taxa de falha (%)", max_digits=5, decimal_places=2, default=Decimal("5.00"))
    simples_pct = models.DecimalField("alíquota do Simples (%)", max_digits=5, decimal_places=2, default=Decimal("4.00"))
    packaging_default_cost = models.DecimalField("embalagem padrão (R$)", max_digits=8, decimal_places=2, default=Decimal("0.00"))
    note = models.CharField("observação", max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "parâmetros globais"
        verbose_name_plural = "parâmetros globais"
        ordering = ["-effective_from", "-id"]

    def __str__(self) -> str:
        return f"Parâmetros desde {self.effective_from:%d/%m/%Y}"

    @classmethod
    def current(cls) -> "GlobalParams":
        today = timezone.localdate()
        obj = cls.objects.filter(effective_from__lte=today).order_by("-effective_from", "-id").first()
        return obj or cls()


class Printer(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Ativa"
        MAINTENANCE = "maintenance", "Em manutenção"
        INACTIVE = "inactive", "Inativa"

    name = models.CharField("nome", max_length=60, unique=True)
    model = models.CharField("modelo", max_length=60, help_text="Bambu Lab A1, H2D, VzBot 330…")
    purchase_price = models.DecimalField("valor para formação de preço (R$)", max_digits=10, decimal_places=2, default=Decimal("3000.00"))
    lifetime_hours = models.PositiveIntegerField("vida útil (horas)", default=5000)
    maintenance_per_hour = models.DecimalField("manutenção por hora (R$)", max_digits=8, decimal_places=2, default=Decimal("0.20"))
    power_w = models.PositiveIntegerField("potência média (W)", default=70)
    status = models.CharField("status", max_length=12, choices=Status.choices, default=Status.ACTIVE)
    notes = models.CharField("observações", max_length=200, blank=True)

    class Meta:
        verbose_name = "impressora"
        verbose_name_plural = "impressoras"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.model})"

    def hourly_cost(self, params: GlobalParams | None = None) -> Decimal:
        """Depreciação + manutenção + energia, exibido e usado com duas casas."""
        from .engine import hourly_cost

        params = params or GlobalParams.current()
        return hourly_cost(
            self.purchase_price, self.lifetime_hours, self.maintenance_per_hour, self.power_w, params.energy_tariff_kwh
        )


class Channel(models.Model):
    class Kind(models.TextChoices):
        MARKETPLACE = "marketplace", "Marketplace"
        SITE = "site", "Site próprio"
        DIRECT = "direct", "Venda direta"
        WHOLESALE = "wholesale", "Atacado"

    class Status(models.TextChoices):
        ACTIVE = "active", "Ativo"
        PAUSED = "paused", "Pausado"

    class PriceList(models.TextChoices):
        RETAIL = "retail", "Varejo"
        WHOLESALE = "wholesale", "Atacado"

    class Export(models.TextChoices):
        NONE = "none", "Nenhum"
        TINY = "tiny", "Tiny ERP"
        ML_CSV = "ml_csv", "Planilha Mercado Livre"
        SHOPEE_CSV = "shopee_csv", "Planilha Shopee"

    name = models.CharField("nome", max_length=60, unique=True)
    kind = models.CharField("tipo", max_length=12, choices=Kind.choices, default=Kind.MARKETPLACE)
    status = models.CharField("status", max_length=8, choices=Status.choices, default=Status.ACTIVE)
    price_list = models.CharField("lista de preço", max_length=10, choices=PriceList.choices, default=PriceList.RETAIL)
    export_format = models.CharField("formato de exportação", max_length=12, choices=Export.choices, default=Export.NONE)
    order = models.PositiveSmallIntegerField("ordem", default=0)
    notes = models.TextField("observações", blank=True)

    class Meta:
        verbose_name = "canal de venda"
        verbose_name_plural = "canais de venda"
        ordering = ["order", "name"]

    def __str__(self) -> str:
        return self.name

    def current_fee(self, on=None) -> "ChannelFee | None":
        on = on or timezone.localdate()
        return self.fees.filter(effective_from__lte=on).order_by("-effective_from", "-id").first()


class ChannelFee(models.Model):
    """Taxas de um canal com data de vigência; o histórico fica guardado."""

    channel = models.ForeignKey(Channel, verbose_name="canal", on_delete=models.CASCADE, related_name="fees")
    effective_from = models.DateField("vigente a partir de", default=timezone.localdate)
    fee_pct = models.DecimalField("taxa (%) sobre o preço", max_digits=5, decimal_places=2, default=Decimal("0"))
    fixed_fee = models.DecimalField("taxa fixa por pedido (R$)", max_digits=8, decimal_places=2, default=Decimal("0"))
    fixed_fee_below_price = models.DecimalField(
        "taxa fixa só abaixo de (R$)", max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="Vazio: sempre cobra. Ex.: Mercado Livre cobra fixo só em itens abaixo de certo valor.",
    )
    shipping_subsidy = models.DecimalField("frete subsidiado pelo vendedor (R$)", max_digits=8, decimal_places=2, default=Decimal("0"))
    shipping_subsidy_from_price = models.DecimalField(
        "frete subsidiado só a partir de (R$)", max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="Vazio: sempre. Ex.: frete grátis obrigatório acima de certo valor.",
    )
    tax_pct_override = models.DecimalField(
        "imposto (%) próprio", max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Vazio usa a alíquota global do Simples.",
    )
    target_margin_pct = models.DecimalField("margem-alvo (%)", max_digits=5, decimal_places=2, default=Decimal("30"))
    min_margin_pct = models.DecimalField("margem mínima (%)", max_digits=5, decimal_places=2, default=Decimal("20"))
    rounding = models.CharField(
        "arredondamento", max_length=10,
        choices=[("ceil_90", "Para cima, final ,90"), ("cents", "Centavos exatos")], default="ceil_90",
    )
    note = models.CharField("observação", max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "taxas do canal"
        verbose_name_plural = "taxas dos canais"
        ordering = ["-effective_from", "-id"]

    def __str__(self) -> str:
        return f"{self.channel} desde {self.effective_from:%d/%m/%Y}"

    def tax_pct(self, params: GlobalParams | None = None) -> Decimal:
        if self.tax_pct_override is not None:
            return self.tax_pct_override
        return (params or GlobalParams.current()).simples_pct


class PriceByChannel(models.Model):
    """Histórico de preço de uma variante em um canal. A linha mais recente é a vigente."""

    variant = models.ForeignKey("catalog.Variant", verbose_name="variante", on_delete=models.CASCADE, related_name="prices")
    channel = models.ForeignKey(Channel, verbose_name="canal", on_delete=models.CASCADE, related_name="prices")
    cost_at = models.DecimalField("custo na data (R$)", max_digits=10, decimal_places=2)
    suggested_price = models.DecimalField("preço sugerido (R$)", max_digits=10, decimal_places=2)
    current_price = models.DecimalField("preço vigente (R$)", max_digits=10, decimal_places=2)
    margin_pct = models.DecimalField("margem resultante (%)", max_digits=6, decimal_places=2)
    below_minimum = models.BooleanField("abaixo da margem mínima", default=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    approved_at = models.DateTimeField("aprovado em", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField("observação", max_length=200, blank=True)

    class Meta:
        verbose_name = "preço por canal"
        verbose_name_plural = "preços por canal"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.variant} @ {self.channel}: R$ {self.current_price}"

    @property
    def approved(self) -> bool:
        return self.approved_at is not None
