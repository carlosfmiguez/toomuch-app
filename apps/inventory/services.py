"""Serviços de estoque: consumo por trabalho de impressão e ajuste por pesagem."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from .models import FilamentSKU, Spool, StockMovement


@transaction.atomic
def consume_filament(job, scrap: bool = False) -> StockMovement:
    """Baixa as gramas de um trabalho no SKU (e no carretel, se houver)."""
    grams = Decimal(job.grams_used)
    fil = FilamentSKU.objects.select_for_update().get(pk=job.filament_id)
    fil.stock_g = fil.stock_g - grams
    fil.save(update_fields=["stock_g"])
    if job.spool_id:
        spool = Spool.objects.select_for_update().get(pk=job.spool_id)
        spool.current_g = max(Decimal("0"), spool.current_g - grams)
        if spool.status == Spool.Status.SEALED:
            spool.status = Spool.Status.IN_USE
        if spool.current_g <= 0:
            spool.status = Spool.Status.EMPTY
        spool.save(update_fields=["current_g", "status"])
    return StockMovement.objects.create(
        kind=StockMovement.Kind.SCRAP if scrap else StockMovement.Kind.CONSUMPTION,
        filament=fil, spool=job.spool if job.spool_id else None, grams=-grams, print_job=job,
        user=job.operator, note=job.failure_reason if scrap else "",
    )


@transaction.atomic
def adjust_by_weighing(filament: FilamentSKU, measured_g: Decimal, user=None, note: str = "") -> StockMovement:
    """Ajusta o saldo do SKU para o valor pesado; registra a diferença como ajuste."""
    filament = FilamentSKU.objects.select_for_update().get(pk=filament.pk)
    diff = Decimal(measured_g) - filament.stock_g
    filament.stock_g = Decimal(measured_g)
    filament.save(update_fields=["stock_g"])
    return StockMovement.objects.create(
        kind=StockMovement.Kind.ADJUSTMENT, filament=filament, grams=diff, user=user, note=note or "Pesagem",
    )


@transaction.atomic
def adjust_spool(spool: Spool, measured_g: Decimal, user=None) -> StockMovement:
    """Pesagem de um carretel: ajusta o rolo e repassa a diferença ao SKU."""
    spool = Spool.objects.select_for_update().get(pk=spool.pk)
    diff = Decimal(measured_g) - spool.current_g
    spool.current_g = Decimal(measured_g)
    if spool.current_g <= 0:
        spool.status = Spool.Status.EMPTY
    spool.save(update_fields=["current_g", "status"])
    fil = FilamentSKU.objects.select_for_update().get(pk=spool.filament_id)
    fil.stock_g = fil.stock_g + diff
    fil.save(update_fields=["stock_g"])
    return StockMovement.objects.create(
        kind=StockMovement.Kind.ADJUSTMENT, filament=fil, spool=spool, grams=diff, user=user, note="Pesagem do carretel",
    )
