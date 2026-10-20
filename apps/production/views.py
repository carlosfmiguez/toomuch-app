from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.costing.models import Printer

from .models import PrintJob, ProductionOrder


def _dec(value) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", ".")) if value not in (None, "") else None
    except InvalidOperation:
        return None


@login_required
def board(request):
    printers = list(Printer.objects.exclude(status=Printer.Status.INACTIVE))
    active = PrintJob.objects.filter(result__in=[PrintJob.Result.PENDING, PrintJob.Result.PRINTING]).select_related(
        "order__variant__product", "part", "printer", "filament", "asset"
    ).order_by("order__priority", "order__due_date", "order_id", "part_id", "sequence")
    columns = []
    for printer in printers:
        jobs = [j for j in active if j.printer_id == printer.pk]
        columns.append({
            "printer": printer,
            "printing": [j for j in jobs if j.result == PrintJob.Result.PRINTING],
            "queued": [j for j in jobs if j.result == PrintJob.Result.PENDING],
        })
    unassigned = [j for j in active if j.printer_id is None]
    finishing = ProductionOrder.objects.filter(
        status__in=[ProductionOrder.Status.POST, ProductionOrder.Status.ASSEMBLY]
    ).select_related("variant__product").order_by("priority", "due_date")
    ready = ProductionOrder.objects.filter(status=ProductionOrder.Status.READY).select_related("variant__product").order_by("-updated_at")[:8]
    return render(request, "production/board.html", {
        "columns": columns, "unassigned": unassigned, "printers": printers, "finishing": finishing, "ready": ready,
    })


@login_required
@require_POST
def job_start(request, pk):
    job = get_object_or_404(PrintJob, pk=pk)
    printer = Printer.objects.filter(pk=request.POST.get("printer")).first() or job.printer
    if printer is None:
        messages.error(request, "Escolha a impressora antes de iniciar.")
        return redirect("production:board")
    job.start(printer=printer, operator=request.user)
    messages.success(request, f"{job.part.name} iniciada em {printer.name}.")
    return redirect("production:board")


@login_required
@require_POST
def job_finish(request, pk):
    job = get_object_or_404(PrintJob, pk=pk)
    grams = _dec(request.POST.get("grams"))
    minutes = request.POST.get("minutes")
    job.finish(ok=True, actual_grams=grams, actual_minutes=int(minutes) if minutes else None, operator=request.user)
    messages.success(request, f"{job.part.name} concluída; {job.grams_used} g baixados de {job.filament or 'filamento não informado'}.")
    return redirect("production:board")


@login_required
@require_POST
def job_fail(request, pk):
    job = get_object_or_404(PrintJob, pk=pk)
    reason = (request.POST.get("reason") or "Falha na impressão").strip()
    grams = _dec(request.POST.get("grams"))
    job.finish(ok=False, actual_grams=grams, failure_reason=reason, operator=request.user)
    messages.warning(request, f"{job.part.name} marcada como falha ({reason}); reimpressão aberta.")
    return redirect("production:board")


@login_required
@require_POST
def order_advance(request, pk):
    order = get_object_or_404(ProductionOrder, pk=pk)
    flow = {
        ProductionOrder.Status.POST: ProductionOrder.Status.ASSEMBLY,
        ProductionOrder.Status.ASSEMBLY: ProductionOrder.Status.READY,
    }
    minutes = request.POST.get("minutes")
    if order.status == ProductionOrder.Status.POST and minutes:
        order.minutes_post_actual = int(minutes)
    elif order.status == ProductionOrder.Status.ASSEMBLY and minutes:
        order.minutes_assembly_actual = int(minutes)
    order.status = flow.get(order.status, order.status)
    if order.status == ProductionOrder.Status.READY:
        variant = order.variant
        variant.stock_ready = variant.stock_ready + order.quantity
        variant.save(update_fields=["stock_ready"])
    order.save()
    messages.success(request, f"{order} → {order.get_status_display()}.")
    return redirect("production:board")
