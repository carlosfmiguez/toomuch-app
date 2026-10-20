from django.contrib import admin, messages
from django.utils.html import format_html

from .models import Asset, BOMItem, Category, Color, Component, Part, Product, Variant
from .services import parse_asset


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "order")
    ordering = ("order",)


@admin.register(Color)
class ColorAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "swatch", "hex")
    search_fields = ("name", "code")

    @admin.display(description="")
    def swatch(self, obj):
        if not obj.hex:
            return ""
        return format_html('<span style="display:inline-block;width:18px;height:18px;border-radius:50%;background:{};border:1px solid #C9B99B"></span>', obj.hex)


class VariantInline(admin.TabularInline):
    model = Variant
    extra = 0
    fields = ("sku", "size", "primary_color", "secondary_color", "main_filament", "secondary_filament", "is_default", "cost_cached")
    readonly_fields = ("cost_cached",)
    show_change_link = True
    autocomplete_fields = ("main_filament", "secondary_filament")


class PartInline(admin.TabularInline):
    model = Part
    extra = 0
    fields = ("name", "default_qty", "color_slot", "waste_pct_override", "failure_pct_override")
    show_change_link = True


class AssetInline(admin.TabularInline):
    model = Asset
    extra = 0
    fk_name = "product"
    fields = ("kind", "file", "name", "is_main", "order", "is_production", "printer", "grams_total", "print_time_min", "parse_error")
    readonly_fields = ("grams_total", "print_time_min", "parse_error")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "sku_base", "category", "status", "origin", "ready", "updated_at")
    list_filter = ("status", "category", "origin")
    search_fields = ("name", "sku_base", "tags")
    inlines = [VariantInline, PartInline, AssetInline]
    fieldsets = (
        (None, {"fields": ("name", "sku_base", "category", "status", "short_description", "long_description", "tags")}),
        ("Medidas e prazo", {"fields": (("height_mm", "width_mm", "depth_mm"), "weight_g", "lead_time_days")}),
        ("Origem e licença", {"fields": (
            "origin", "license_author", "license_url", "license_terms", "license_cost_per_unit",
            "license_valid_until", "license_attribution_required",
        )}),
    )

    @admin.display(description="pronto p/ catálogo", boolean=True)
    def ready(self, obj):
        return obj.ready_for_catalog


class BOMItemInline(admin.TabularInline):
    model = BOMItem
    extra = 0
    fields = ("kind", "part", "component", "quantity", "color_slot")
    autocomplete_fields = ("component",)


@admin.register(Variant)
class VariantAdmin(admin.ModelAdmin):
    list_display = ("sku", "product", "size", "primary_color", "cost_cached", "cost_cached_at", "stock_ready")
    list_filter = ("product__category", "size")
    search_fields = ("sku", "product__name")
    inlines = [BOMItemInline]
    autocomplete_fields = ("product", "main_filament", "secondary_filament")
    readonly_fields = ("cost_cached", "cost_cached_at", "cost_breakdown")
    actions = ["recalculate"]

    @admin.action(description="Recalcular custo e preços sugeridos")
    def recalculate(self, request, queryset):
        from apps.costing.services import compute_prices, compute_variant_cost

        for variant in queryset:
            _, problems = compute_variant_cost(variant)
            compute_prices(variant)
            for p in problems:
                messages.warning(request, f"{variant.sku}: {p.message}")
        messages.success(request, f"{queryset.count()} variante(s) recalculada(s).")


class PartAssetInline(admin.TabularInline):
    model = Asset
    extra = 0
    fk_name = "part"
    fields = ("kind", "file", "printer", "is_production", "version", "grams_total", "print_time_min", "parse_error")
    readonly_fields = ("grams_total", "print_time_min", "parse_error")
    verbose_name = "arquivo da peça (3MF fatiado, STL, STEP)"
    verbose_name_plural = "arquivos da peça"


@admin.register(Part)
class PartAdmin(admin.ModelAdmin):
    list_display = ("name", "product", "default_qty", "color_slot", "has_production_file")
    list_filter = ("product__category",)
    search_fields = ("name", "product__name")
    inlines = [PartAssetInline]
    autocomplete_fields = ("product",)

    @admin.display(description="3MF de produção", boolean=True)
    def has_production_file(self, obj):
        return obj.production_assets().exists()


@admin.register(Component)
class ComponentAdmin(admin.ModelAdmin):
    list_display = ("name", "unit_cost", "stock_qty", "min_stock", "is_packaging", "supplier", "active")
    list_filter = ("is_packaging", "active")
    search_fields = ("name", "supplier")


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "product", "part", "printer", "is_production", "grams_total", "print_time_min", "parsed_at", "parse_error")
    list_filter = ("kind", "is_production", "printer")
    search_fields = ("name", "product__name", "part__name", "sha256")
    readonly_fields = ("sha256", "size_bytes", "grams_total", "print_time_min", "meta", "parsed_at", "parse_error", "thumbnail_preview")
    autocomplete_fields = ("product",)
    actions = ["reparse"]

    @admin.display(description="thumbnail")
    def thumbnail_preview(self, obj):
        if obj.thumbnail:
            return format_html('<img src="{}" style="max-width:240px;border-radius:8px">', obj.thumbnail.url)
        return "—"

    @admin.action(description="Ler metadados de novo")
    def reparse(self, request, queryset):
        for asset in queryset:
            asset.parsed_at = None
            asset.parse_error = ""
            parse_asset(asset)
        messages.success(request, f"{queryset.count()} arquivo(s) relido(s).")
