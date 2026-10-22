"""
Inventário: filamento por SKU (com custo médio ponderado), carretel opcional,
compras e movimentos de estoque.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

SIX = Decimal("0.000001")
TWO = Decimal("0.01")


class FilamentSKU(models.Model):
    brand = models.CharField("marca", max_length=80)
    manufacturer = models.CharField("fabricante", max_length=80, blank=True)
    material = models.CharField("tipo", max_length=40, help_text="PLA, PLA Silk, PLA Matte, PETG…")
    color_name = models.CharField("cor (nome oficial do fabricante)", max_length=80)
    color_hex = models.CharField("hex", max_length=7, blank=True)
    color = models.ForeignKey(
        "catalog.Color", verbose_name="código de cor no SKU", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="filaments",
    )
    diameter_mm = models.DecimalField("diâmetro (mm)", max_digits=4, decimal_places=2, default=Decimal("1.75"))
    density_g_cm3 = models.DecimalField("densidade (g/cm³)", max_digits=5, decimal_places=3, default=Decimal("1.240"))
    avg_cost_per_g = models.DecimalField("custo médio por grama (R$)", max_digits=12, decimal_places=6, default=Decimal("0"))
    stock_g = models.DecimalField("saldo (g)", max_digits=12, decimal_places=2, default=Decimal("0"))
    min_stock_g = models.DecimalField("estoque mínimo (g)", max_digits=12, decimal_places=2, default=Decimal("500"))
    supplier = models.CharField("fornecedor", max_length=120, blank=True)
    url = models.URLField("link de compra", blank=True)
    active = models.BooleanField("ativo", default=True)
    notes = models.CharField("observações", max_length=200, blank=True)

    class Meta:
        verbose_name = "filamento (SKU)"
        verbose_name_plural = "filamentos (SKU)"
        ordering = ["brand", "material", "color_name"]
        constraints = [
            models.UniqueConstraint(fields=["brand", "material", "color_name"], name="uniq_filament_sku"),
        ]

    def __str__(self) -> str:
        return f"{self.brand} {self.material} {self.color_name}"

    @property
    def avg_cost_per_kg(self) -> Decimal:
        return (self.avg_cost_per_g * 1000).quantize(TWO, ROUND_HALF_UP)

    @property
    def below_minimum(self) -> bool:
        return self.stock_g < self.min_stock_g

    def receive(self, grams: Decimal, cost_per_g: Decimal) -> None:
        """Entrada de compra: média ponderada entre o saldo atual e as gramas compradas."""
        grams = Decimal(grams)
        cost_per_g = Decimal(cost_per_g)
        total = self.stock_g + grams
        if total <= 0:
            self.avg_cost_per_g = cost_per_g
        else:
            self.avg_cost_per_g = ((self.stock_g * self.avg_cost_per_g + grams * cost_per_g) / total).quantize(SIX)
        self.stock_g = total


def recompute_filament_from_purchases(fil: "FilamentSKU") -> None:
    """Custo médio = valor total (itens + frete) ÷ gramas de todas as compras lançadas deste filamento."""
    grams, value = Decimal("0"), Decimal("0")
    for p in fil.purchases.filter(applied=True):
        if p.grams_total:
            grams += p.grams_total
            value += p.landed_cost
    if grams > 0:
        fil.avg_cost_per_g = (value / grams).quantize(SIX)
        fil.save(update_fields=["avg_cost_per_g"])


class Spool(models.Model):
    class Status(models.TextChoices):
        SEALED = "sealed", "Lacrado"
        IN_USE = "in_use", "Em uso"
        EMPTY = "empty", "Vazio"

    filament = models.ForeignKey(FilamentSKU, verbose_name="filamento", on_delete=models.PROTECT, related_name="spools")
    label = models.CharField("etiqueta", max_length=40, blank=True, help_text="Código impresso no rolo / QR")
    lot = models.CharField("lote", max_length=60, blank=True)
    initial_g = models.DecimalField("peso inicial (g)", max_digits=10, decimal_places=2, default=Decimal("1000"))
    current_g = models.DecimalField("peso atual (g)", max_digits=10, decimal_places=2, default=Decimal("1000"))
    purchase_cost = models.DecimalField("custo de compra (R$)", max_digits=10, decimal_places=2, null=True, blank=True)
    purchased_at = models.DateField("data da compra", null=True, blank=True)
    printer = models.ForeignKey(
        "costing.Printer", verbose_name="impressora", on_delete=models.SET_NULL, null=True, blank=True, related_name="spools"
    )
    slot = models.PositiveSmallIntegerField("slot do AMS", null=True, blank=True)
    status = models.CharField("status", max_length=10, choices=Status.choices, default=Status.SEALED)

    class Meta:
        verbose_name = "carretel"
        verbose_name_plural = "carretéis"
        ordering = ["filament", "status", "id"]

    def __str__(self) -> str:
        return f"{self.label or f'#{self.pk}'} · {self.filament}"


class Purchase(models.Model):
    """Compra de filamento ou de componente. Ao salvar, gera o movimento e atualiza o custo médio."""

    filament = models.ForeignKey(FilamentSKU, verbose_name="filamento", on_delete=models.PROTECT, null=True, blank=True, related_name="purchases")
    component = models.ForeignKey(
        "catalog.Component", verbose_name="componente", on_delete=models.PROTECT, null=True, blank=True, related_name="purchases"
    )
    quantity = models.DecimalField("quantidade (rolos ou unidades)", max_digits=10, decimal_places=2, default=Decimal("1"))
    grams_total = models.DecimalField("gramas no total (filamento)", max_digits=12, decimal_places=2, null=True, blank=True)
    total_cost = models.DecimalField("valor dos itens (R$)", max_digits=10, decimal_places=2)
    shipping_cost = models.DecimalField("frete rateado (R$)", max_digits=10, decimal_places=2, default=Decimal("0.00"))
    supplier = models.CharField("fornecedor", max_length=120, blank=True)
    purchased_at = models.DateField("data", default=timezone.localdate)
    invoice_ref = models.CharField("nota / pedido", max_length=60, blank=True)
    applied = models.BooleanField("aplicada ao estoque", default=False, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "compra"
        verbose_name_plural = "compras"
        ordering = ["-purchased_at", "-id"]

    def __str__(self) -> str:
        return f"Compra {self.purchased_at} · {self.filament or self.component}"

    @property
    def landed_cost(self) -> Decimal:
        return self.total_cost + self.shipping_cost

    @property
    def cost_per_g(self) -> Decimal | None:
        if self.grams_total:
            return (self.landed_cost / self.grams_total).quantize(SIX)
        return None

    def clean(self):
        from django.core.exceptions import ValidationError

        if bool(self.filament_id) == bool(self.component_id):
            raise ValidationError("Informe o filamento ou o componente, um dos dois.")
        if self.filament_id and not self.grams_total:
            raise ValidationError({"grams_total": "Informe as gramas totais da compra de filamento."})

    @transaction.atomic
    def apply(self) -> None:
        """Lança a compra no estoque uma única vez."""
        if self.applied:
            return
        if self.filament_id:
            fil = FilamentSKU.objects.select_for_update().get(pk=self.filament_id)
            fil.receive(self.grams_total, self.cost_per_g)
            fil.save(update_fields=["avg_cost_per_g", "stock_g"])
            StockMovement.objects.create(
                kind=StockMovement.Kind.PURCHASE, filament=fil, grams=self.grams_total,
                purchase=self, note=f"Compra {self.invoice_ref}".strip(),
            )
        else:
            comp = self.component
            comp.stock_qty = comp.stock_qty + self.quantity
            if self.quantity:
                comp.unit_cost = (self.landed_cost / self.quantity).quantize(TWO, ROUND_HALF_UP)
            comp.save(update_fields=["stock_qty", "unit_cost"])
            StockMovement.objects.create(
                kind=StockMovement.Kind.PURCHASE, component=comp, quantity=self.quantity,
                purchase=self, note=f"Compra {self.invoice_ref}".strip(),
            )
        self.applied = True
        self.save(update_fields=["applied"])


class StockMovement(models.Model):
    """Movimento de estoque: nunca editado, só estornado com outro movimento."""

    class Kind(models.TextChoices):
        PURCHASE = "purchase", "Compra"
        CONSUMPTION = "consumption", "Consumo"
        SCRAP = "scrap", "Refugo"
        ADJUSTMENT = "adjustment", "Ajuste (pesagem)"
        REVERSAL = "reversal", "Estorno"

    kind = models.CharField("tipo", max_length=12, choices=Kind.choices)
    filament = models.ForeignKey(FilamentSKU, on_delete=models.PROTECT, null=True, blank=True, related_name="movements", verbose_name="filamento")
    spool = models.ForeignKey(Spool, on_delete=models.SET_NULL, null=True, blank=True, related_name="movements", verbose_name="carretel")
    component = models.ForeignKey("catalog.Component", on_delete=models.PROTECT, null=True, blank=True, related_name="movements", verbose_name="componente")
    grams = models.DecimalField("gramas (+ entrada / − saída)", max_digits=12, decimal_places=2, null=True, blank=True)
    quantity = models.DecimalField("quantidade (+ / −)", max_digits=10, decimal_places=2, null=True, blank=True)
    purchase = models.ForeignKey(Purchase, on_delete=models.SET_NULL, null=True, blank=True, related_name="movements")
    print_job = models.ForeignKey("production.PrintJob", on_delete=models.SET_NULL, null=True, blank=True, related_name="movements")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    note = models.CharField("observação", max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "movimento de estoque"
        verbose_name_plural = "movimentos de estoque"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        what = self.filament or self.component
        amount = self.grams if self.grams is not None else self.quantity
        return f"{self.get_kind_display()} · {what} · {amount}"
