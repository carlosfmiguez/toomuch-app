from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.catalog.models import Variant
from apps.core.dashboard import shell

from . import engine
from .forms import QuickCalculatorForm
from .models import Channel, GlobalParams
from .services import latest_prices, terms_for


@login_required
def calculator(request):
    params = GlobalParams.current()
    form = QuickCalculatorForm(request.POST or None)
    result = None
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        printer = d.get("printer")
        hourly = printer.hourly_cost(params) if printer else Decimal("0")
        inp = engine.CostInput(
            filaments=[engine.FilamentUse(d["grams"], d["cost_per_kg"] / Decimal(1000), label="filamento")],
            machines=[engine.MachineUse(d["hours"], hourly, label=str(printer) if printer else "sem impressora")],
            components=[engine.ComponentUse(Decimal(1), d["components"], "componentes")] if d["components"] else [],
            labor_minutes=Decimal(d["labor_minutes"]), labor_rate_per_hour=params.labor_cost_per_hour,
            packaging_cost=d["packaging"], license_cost=d.get("license_cost") or Decimal("0"),
            waste_pct=d.get("waste_pct") if d.get("waste_pct") is not None else params.waste_pct,
            failure_pct=d.get("failure_pct") if d.get("failure_pct") is not None else params.failure_pct,
        )
        breakdown = engine.compute_cost(inp)
        channels = []
        for channel in Channel.objects.filter(status=Channel.Status.ACTIVE):
            fee = channel.current_fee()
            if not fee:
                continue
            terms = terms_for(fee, params)
            price = engine.suggest_price(breakdown.total, terms)
            channels.append({"channel": channel, "fee": fee, "price": price})
        result = {"breakdown": breakdown, "hourly": hourly, "channels": channels}
    if request.headers.get("HX-Request"):
        return render(request, "costing/_calculator_result.html", {"form": form, "result": result, "params": params})
    return render(request, "costing/calculator.html", {**shell(request, "Calculadora rápida"), "form": form, "result": result, "params": params})


@login_required
def price_list(request):
    channels = list(Channel.objects.filter(status=Channel.Status.ACTIVE))
    rows = []
    for variant in Variant.objects.select_related("product", "primary_color").exclude(product__status="discontinued"):
        by_channel = {p.channel_id: p for p in latest_prices(variant)}
        rows.append({"variant": variant, "cells": [by_channel.get(c.pk) for c in channels]})
    return render(request, "costing/price_list.html", {**shell(request, "Lista de preço"), "channels": channels, "rows": rows})


@login_required
def approvals(request):
    from apps.catalog.workflow import pending_approvals

    return render(request, "costing/approvals.html", {**shell(request, "Aprovar preços"), "items": pending_approvals()})
