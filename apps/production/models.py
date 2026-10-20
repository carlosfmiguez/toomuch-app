"""
Produção: ordem de produção (uma variante × quantidade) que explode em trabalhos
de impressão por peça, cada um com impressora, 3MF, filamento e resultado.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone


class ProductionOrder(models.Model):
    class Origin(models.TextChoices):
        TINY = "tiny", "Pedido do Tiny"
        STOCK = "stock", "Reposição de estoque"
        SAMPLE = "sample", "Amostra / teste"
        CUSTOM = "custom", "Encomenda direta"

    class Status(models.TextChoices):
        QUEUED = "queued", "Na fila"
        PRINTING = "printing", "Imprimindo"
        POST = "post", "Pós-processamento"
        ASSEMBLY = "assembly", "Montagem"
        READY = "ready", "Pronto"
        CANCELLED = "cancelled", "Cancelada"

    origin = models.CharField("origem", max_length=10, choices=Origin.choices, default=Origin.STOCK)
    external_id = models.CharField("id externo (pedido)", max_length=60, blank=True)
    variant = models.ForeignKey("catalog.Variant", verbose_name="variante", on_delete=models.PROTECT, related_name="orders")
    quantity = models.PositiveIntegerField("quantidade", default=1)
    priority = models.PositiveSmallIntegerField("prioridade", default=3, help_text="1 = urgente, 5 = quando der")
    due_date = models.DateField("prazo", null=True, blank=True)
    status = models.CharField("status", max_length=10, choices=Status.choices, default=Status.QUEUED)
    printer = models.ForeignKey(
        "costing.Printer", verbose_name="impressora preferida", on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
    notes = models.TextField("observações", blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    minutes_post_actual = models.PositiveSmallIntegerField("pós-processamento real (min)", null=True, blank=True)
    minutes_assembly_actual = models.PositiveSmallIntegerField("montagem real (min)", null=True, blank=True)
    minutes_packaging_actual = models.PositiveSmallIntegerField("embalagem real (min)", null=True, blank=True)

    class Meta:
        verbose_name = "ordem de produção"
        verbose_name_plural = "ordens de produção"
        ordering = ["status", "priority", "due_date", "id"]

    def __str__(self) -> str:
        return f"OP-{self.pk} · {self.quantity} × {self.variant}"

    @transaction.atomic
    def explode(self, printer=None) -> list["PrintJob"]:
        """Cria um trabalho de impressão por peça da BOM × quantidade (se ainda não houver)."""
        from apps.catalog.models import BOMItem

        if self.jobs.exists():
            return list(self.jobs.all())
        printer = printer or self.printer
        jobs = []
        items = self.variant.bom_items.filter(kind=BOMItem.Kind.PART).select_related("part")
        for item in items:
            part = item.part
            asset = part.production_asset(printer)
            filament = self.variant.filament_for_slot(item.effective_slot)
            copies = int(Decimal(item.quantity) * self.quantity)
            for n in range(copies):
                jobs.append(PrintJob.objects.create(
                    order=self, part=part, printer=printer, asset=asset, filament=filament,
                    est_grams=asset.grams_total if asset else None,
                    est_minutes=asset.print_time_min if asset else None,
                    sequence=n + 1,
                ))
        return jobs


class PrintJob(models.Model):
    class Result(models.TextChoices):
        PENDING = "pending", "Pendente"
        PRINTING = "printing", "Imprimindo"
        OK = "ok", "Concluído"
        FAILED = "failed", "Falhou"

    order = models.ForeignKey(ProductionOrder, verbose_name="ordem", on_delete=models.CASCADE, related_name="jobs")
    part = models.ForeignKey("catalog.Part", verbose_name="peça", on_delete=models.PROTECT, related_name="jobs")
    sequence = models.PositiveSmallIntegerField("cópia nº", default=1)
    printer = models.ForeignKey("costing.Printer", verbose_name="impressora", on_delete=models.SET_NULL, null=True, blank=True, related_name="jobs")
    asset = models.ForeignKey("catalog.Asset", verbose_name="3MF usado", on_delete=models.SET_NULL, null=True, blank=True, related_name="jobs")
    filament = models.ForeignKey("inventory.FilamentSKU", verbose_name="filamento", on_delete=models.SET_NULL, null=True, blank=True, related_name="jobs")
    spool = models.ForeignKey("inventory.Spool", verbose_name="carretel", on_delete=models.SET_NULL, null=True, blank=True, related_name="jobs")
    started_at = models.DateTimeField("início", null=True, blank=True)
    finished_at = models.DateTimeField("fim", null=True, blank=True)
    est_minutes = models.PositiveIntegerField("tempo estimado (min)", null=True, blank=True)
    actual_minutes = models.PositiveIntegerField("tempo real (min)", null=True, blank=True)
    est_grams = models.DecimalField("gramas estimadas", max_digits=10, decimal_places=2, null=True, blank=True)
    actual_grams = models.DecimalField("gramas reais (pesagem)", max_digits=10, decimal_places=2, null=True, blank=True)
    result = models.CharField("resultado", max_length=10, choices=Result.choices, default=Result.PENDING)
    failure_reason = models.CharField("motivo da falha", max_length=200, blank=True)
    operator = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="operador", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    notes = models.CharField("observações", max_length=200, blank=True)
    stock_applied = models.BooleanField("baixa de estoque feita", default=False, editable=False)

    class Meta:
        verbose_name = "trabalho de impressão"
        verbose_name_plural = "trabalhos de impressão"
        ordering = ["order", "part", "sequence"]

    def __str__(self) -> str:
        return f"{self.order} · {self.part.name} #{self.sequence}"

    @property
    def grams_used(self) -> Decimal | None:
        return self.actual_grams if self.actual_grams is not None else self.est_grams

    def start(self, printer=None, operator=None) -> None:
        self.started_at = timezone.now()
        self.result = self.Result.PRINTING
        if printer:
            self.printer = printer
        if operator:
            self.operator = operator
        self.save()
        if self.order.status == ProductionOrder.Status.QUEUED:
            self.order.status = ProductionOrder.Status.PRINTING
            self.order.save(update_fields=["status"])

    @transaction.atomic
    def finish(self, ok: bool, actual_grams=None, actual_minutes=None, failure_reason: str = "", operator=None) -> None:
        """Fecha o trabalho e baixa o filamento (consumo ou refugo) uma única vez."""
        from apps.inventory.services import consume_filament

        self.finished_at = timezone.now()
        self.result = self.Result.OK if ok else self.Result.FAILED
        if actual_grams is not None:
            self.actual_grams = actual_grams
        if actual_minutes is not None:
            self.actual_minutes = actual_minutes
        elif self.started_at:
            self.actual_minutes = int((self.finished_at - self.started_at).total_seconds() // 60)
        self.failure_reason = failure_reason
        if operator:
            self.operator = operator
        self.save()
        if not self.stock_applied and self.filament_id and self.grams_used:
            consume_filament(self, scrap=not ok)
            self.stock_applied = True
            self.save(update_fields=["stock_applied"])
        if not ok:
            # abre a reimpressão
            PrintJob.objects.create(
                order=self.order, part=self.part, printer=self.printer, asset=self.asset, filament=self.filament,
                est_grams=self.est_grams, est_minutes=self.est_minutes, sequence=self.sequence,
                notes=f"Reimpressão de #{self.pk}",
            )
        self.order.refresh_status()


def _refresh_status(self: ProductionOrder) -> None:
    jobs = list(self.jobs.all())
    if not jobs:
        return
    if all(j.result == PrintJob.Result.OK for j in jobs):
        if self.status in (ProductionOrder.Status.QUEUED, ProductionOrder.Status.PRINTING):
            self.status = ProductionOrder.Status.POST
            self.save(update_fields=["status"])
    elif any(j.result == PrintJob.Result.PRINTING for j in jobs) and self.status == ProductionOrder.Status.QUEUED:
        self.status = ProductionOrder.Status.PRINTING
        self.save(update_fields=["status"])


ProductionOrder.refresh_status = _refresh_status
