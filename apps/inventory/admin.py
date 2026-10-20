from django.contrib import admin, messages
from django.utils.html import format_html

from .models import FilamentSKU, Purchase, Spool, StockMovement


class SpoolInline(admin.TabularInline):
    model = Spool
    extra = 0
    fields = ("label", "lot", "initial_g", "current_g", "printer", "slot", "status")


@admin.register(FilamentSKU)
class FilamentSKUAdmin(admin.ModelAdmin):
    list_display = ("__str__", "manufacturer", "swatch", "stock_g", "min_stock_g", "avg_cost_per_kg", "low", "active")
    list_filter = ("brand", "material", "active")
    search_fields = ("brand", "manufacturer", "material", "color_name")
    inlines = [SpoolInline]
    readonly_fields = ("avg_cost_per_g", "stock_g")
    fieldsets = (
        (None, {"fields": (("brand", "manufacturer"), "material", ("color_name", "color_hex", "color"))}),
        ("Material", {"fields": (("diameter_mm", "density_g_cm3"),)}),
        ("Estoque e custo (atualizados pelas compras e pela produção)", {"fields": (("stock_g", "min_stock_g"), "avg_cost_per_g")}),
        ("Compra", {"fields": ("supplier", "url", "active", "notes")}),
    )

    @admin.display(description="")
    def swatch(self, obj):
        if not obj.color_hex:
            return ""
        return format_html('<span style="display:inline-block;width:18px;height:18px;border-radius:50%;background:{};border:1px solid #C9B99B"></span>', obj.color_hex)

    @admin.display(description="abaixo do mínimo", boolean=True)
    def low(self, obj):
        return obj.below_minimum


@admin.register(Spool)
class SpoolAdmin(admin.ModelAdmin):
    list_display = ("__str__", "status", "current_g", "initial_g", "printer", "slot", "purchased_at")
    list_filter = ("status", "printer", "filament__material")
    search_fields = ("label", "lot", "filament__brand", "filament__color_name")
    autocomplete_fields = ("filament",)


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ("purchased_at", "filament", "component", "quantity", "grams_total", "total_cost", "shipping_cost", "cost_per_g", "applied")
    list_filter = ("purchased_at", "applied")
    search_fields = ("supplier", "invoice_ref", "filament__brand", "filament__color_name", "component__name")
    autocomplete_fields = ("filament", "component")
    readonly_fields = ("applied",)
    actions = ["apply_to_stock"]

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        if not obj.applied:
            obj.apply()
            messages.success(request, "Compra lançada no estoque e custo médio atualizado.")

    @admin.action(description="Lançar no estoque (se ainda não lançada)")
    def apply_to_stock(self, request, queryset):
        n = 0
        for purchase in queryset:
            if not purchase.applied:
                purchase.apply()
                n += 1
        messages.success(request, f"{n} compra(s) lançada(s).")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind", "filament", "component", "spool", "grams", "quantity", "print_job", "purchase", "user")
    list_filter = ("kind", "filament__material")
    search_fields = ("filament__brand", "filament__color_name", "component__name", "note")
    readonly_fields = [f.name for f in StockMovement._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
