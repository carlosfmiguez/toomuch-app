from django.contrib import admin, messages
from unfold.admin import ModelAdmin, TabularInline

from .models import Channel, ChannelFee, GlobalParams, PriceByChannel, Printer
from .services import approve_price


@admin.register(GlobalParams)
class GlobalParamsAdmin(ModelAdmin):
    list_display = ("effective_from", "energy_tariff_kwh", "labor_cost_per_hour", "waste_pct", "failure_pct", "simples_pct", "packaging_default_cost")
    ordering = ("-effective_from",)


@admin.register(Printer)
class PrinterAdmin(ModelAdmin):
    list_display = ("name", "model", "status", "purchase_price", "lifetime_hours", "maintenance_per_hour", "power_w", "hourly")
    list_filter = ("status", "model")

    @admin.display(description="custo/hora (R$)")
    def hourly(self, obj):
        return obj.hourly_cost()


class ChannelFeeInline(TabularInline):
    model = ChannelFee
    extra = 0
    fields = (
        "effective_from", "fee_pct", "fixed_fee", "fixed_fee_below_price", "shipping_subsidy", "shipping_subsidy_from_price",
        "tax_pct_override", "target_margin_pct", "min_margin_pct", "rounding", "note",
    )
    ordering = ("-effective_from",)


@admin.register(Channel)
class ChannelAdmin(ModelAdmin):
    list_display = ("name", "kind", "status", "price_list", "export_format", "fee_now", "margin_now", "order")
    list_filter = ("kind", "status")
    inlines = [ChannelFeeInline]
    actions = ["recompute_prices"]

    @admin.display(description="taxa vigente")
    def fee_now(self, obj):
        fee = obj.current_fee()
        if not fee:
            return "—"
        bits = [f"{fee.fee_pct}%"]
        if fee.fixed_fee:
            bits.append(f"+ R$ {fee.fixed_fee}")
        if fee.shipping_subsidy:
            bits.append(f"+ frete R$ {fee.shipping_subsidy}")
        return " ".join(bits)

    @admin.display(description="margem-alvo")
    def margin_now(self, obj):
        fee = obj.current_fee()
        return f"{fee.target_margin_pct}% (mín. {fee.min_margin_pct}%)" if fee else "—"

    @admin.action(description="Recalcular preços sugeridos de todas as variantes")
    def recompute_prices(self, request, queryset):
        from .services import recompute_all

        n = recompute_all()
        messages.success(request, f"Custos e preços recalculados para {n} variante(s).")


@admin.register(PriceByChannel)
class PriceByChannelAdmin(ModelAdmin):
    list_display = ("variant", "channel", "cost_at", "suggested_price", "current_price", "margin_pct", "below_minimum", "approved_at", "approved_by", "created_at")
    list_filter = ("channel", "below_minimum", ("approved_at", admin.EmptyFieldListFilter))
    search_fields = ("variant__sku", "variant__product__name")
    readonly_fields = ("variant", "channel", "cost_at", "suggested_price", "margin_pct", "below_minimum", "approved_by", "approved_at", "created_at")
    actions = ["approve_suggested", "approve_current"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Aprovar o preço sugerido como vigente")
    def approve_suggested(self, request, queryset):
        for row in queryset:
            approve_price(row, request.user, price=row.suggested_price)
        messages.success(request, f"{queryset.count()} preço(s) aprovado(s).")

    @admin.action(description="Aprovar o preço vigente informado")
    def approve_current(self, request, queryset):
        for row in queryset:
            approve_price(row, request.user, price=row.current_price)
        messages.success(request, f"{queryset.count()} preço(s) aprovado(s).")
