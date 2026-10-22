from django import forms
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline

from .models import Asset, BOMItem, Category, Color, Component, Part, Product, Variant
from .services import parse_asset

MODEL_KINDS = (Asset.Kind.THREEMF_SLICED, Asset.Kind.THREEMF_PROJECT, Asset.Kind.STL, Asset.Kind.STEP, Asset.Kind.F3D)
MEDIA_KINDS = (Asset.Kind.PHOTO, Asset.Kind.RENDER, Asset.Kind.PDF, Asset.Kind.OTHER)


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    list_display = ("name", "code", "order")
    ordering = ("order",)


@admin.register(Color)
class ColorAdmin(ModelAdmin):
    list_display = ("name", "code", "swatch", "hex")
    search_fields = ("name", "code")

    @admin.display(description="")
    def swatch(self, obj):
        if not obj.hex:
            return ""
        return format_html('<span style="display:inline-block;width:18px;height:18px;border-radius:50%;background:{};border:1px solid #C9B99B"></span>', obj.hex)


class VariantInline(TabularInline):
    model = Variant
    extra = 0
    fields = ("sku", "size", "primary_color", "secondary_color", "main_filament", "secondary_filament", "is_default", "cost_cached", "prices_now")
    readonly_fields = ("cost_cached", "prices_now")

    @admin.display(description="preços vigentes")
    def prices_now(self, obj):
        from apps.costing.services import latest_prices

        if not obj.pk:
            return "—"
        rows = latest_prices(obj)
        if not rows:
            return "—"
        return " · ".join(f"{r.channel.name} {r.current_price}{'' if r.approved else ' (a aprovar)'}" for r in rows)
    show_change_link = True
    autocomplete_fields = ("main_filament", "secondary_filament")


class PartInline(TabularInline):
    model = Part
    extra = 0
    fields = ("name", "default_qty", "color_slot", "waste_pct_override", "failure_pct_override")
    show_change_link = True
    verbose_name_plural = "peças impressas (o que sai da impressora; os arquivos vão na seção seguinte)"


class PartFileForm(forms.ModelForm):
    """Arquivo 3D no cadastro do produto: obriga a escolher a peça e já vem como 3MF de produção."""

    class Meta:
        model = Asset
        fields = ("part", "kind", "file", "printer", "is_production", "version")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].choices = [(k, label) for k, label in Asset.Kind.choices if k in MODEL_KINDS]
        if not self.instance.pk:
            self.fields["kind"].initial = Asset.Kind.THREEMF_SLICED
            self.fields["is_production"].initial = True
        self.fields["part"].label = "peça"
        self.fields["part"].help_text = "Salve o produto depois de criar a peça para ela aparecer aqui."

    def clean(self):
        data = super().clean()
        if data.get("file") and not data.get("part") and not data.get("DELETE"):
            raise forms.ValidationError({"part": "Escolha a peça a que este arquivo pertence."})
        return data


class PartFileInline(TabularInline):
    """3MF fatiado (e STL/STEP) por peça, direto no cadastro do produto."""

    model = Asset
    form = PartFileForm
    extra = 0
    fk_name = "product"
    fields = ("part", "kind", "file", "printer", "is_production", "version", "grams_total", "print_time_min", "parse_error")
    readonly_fields = ("grams_total", "print_time_min", "parse_error")
    verbose_name = "arquivo 3D de uma peça"
    verbose_name_plural = "3MF fatiado por peça (exporte do Bambu Studio em Arquivo → Exportar → arquivo fatiado da placa; é o que alimenta o custo)"

    def get_queryset(self, request):
        return super().get_queryset(request).filter(kind__in=MODEL_KINDS)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "part":
            product_id = getattr(request, "_toomuch_product_id", None)
            kwargs["queryset"] = Part.objects.filter(product_id=product_id) if product_id else Part.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class AssetInline(TabularInline):
    model = Asset
    extra = 0
    fk_name = "product"
    fields = ("kind", "file", "name", "is_main", "order")
    verbose_name = "foto, render ou PDF do produto"
    verbose_name_plural = "fotos, renders e PDFs do produto (a foto principal vai para o catálogo e a loja)"

    def get_queryset(self, request):
        return super().get_queryset(request).filter(kind__in=MEDIA_KINDS)

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.form.base_fields["kind"].choices = [(k, label) for k, label in Asset.Kind.choices if k in MEDIA_KINDS]
        formset.form.base_fields["kind"].initial = Asset.Kind.PHOTO
        return formset


@admin.register(Product)
class ProductAdmin(ModelAdmin):
    list_display = ("name", "sku_base", "category", "status", "origin", "ready", "updated_at")
    list_filter = ("status", "category", "origin")
    search_fields = ("name", "sku_base", "tags")
    inlines = [PartInline, PartFileInline, VariantInline, AssetInline]
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

    def get_form(self, request, obj=None, **kwargs):
        request._toomuch_product_id = obj.pk if obj else None  # para o inline de arquivos listar só as peças deste produto
        return super().get_form(request, obj, **kwargs)

    def save_related(self, request, form, formsets, change):
        from .workflow import refresh_product, sync_bom_with_parts

        super().save_related(request, form, formsets, change)
        # Variante sem composição ganha uma linha por peça; peça nova entra nas variantes; custo e preço recalculados.
        n = sum(v.ensure_default_bom() for v in form.instance.variants.all())
        sync_bom_with_parts(form.instance)
        refresh_product(form.instance)
        if n:
            messages.info(request, f"Composição criada automaticamente com {n} peça(s); ajuste na variante se precisar.")

    def _to_ficha(self, request, obj, response):
        if "_continue" in request.POST or "_addanother" in request.POST or "_saveasnew" in request.POST:
            return response
        return redirect(obj.get_absolute_url())

    def response_add(self, request, obj, post_url_continue=None):
        return self._to_ficha(request, obj, super().response_add(request, obj, post_url_continue))

    def response_change(self, request, obj):
        return self._to_ficha(request, obj, super().response_change(request, obj))


class BOMItemInline(TabularInline):
    model = BOMItem
    extra = 0
    fields = ("kind", "part", "component", "quantity", "color_slot")
    autocomplete_fields = ("component",)


@admin.register(Variant)
class VariantAdmin(ModelAdmin):
    list_display = ("sku", "product", "size", "primary_color", "cost_cached", "cost_cached_at", "stock_ready")
    list_filter = ("product__category", "size")
    search_fields = ("sku", "product__name")
    inlines = [BOMItemInline]
    autocomplete_fields = ("product", "main_filament", "secondary_filament")
    readonly_fields = ("cost_cached", "cost_cached_at", "cost_breakdown")
    actions = ["recalculate"]

    def save_related(self, request, form, formsets, change):
        from .workflow import refresh_variant

        super().save_related(request, form, formsets, change)
        n = form.instance.ensure_default_bom()
        refresh_variant(form.instance)
        if n:
            messages.info(request, f"Composição criada automaticamente com {n} peça(s).")

    @admin.action(description="Recalcular custo e preços sugeridos")
    def recalculate(self, request, queryset):
        from apps.costing.services import compute_prices, compute_variant_cost

        for variant in queryset:
            _, problems = compute_variant_cost(variant)
            compute_prices(variant)
            for p in problems:
                messages.warning(request, f"{variant.sku}: {p.message}")
        messages.success(request, f"{queryset.count()} variante(s) recalculada(s).")


class PartAssetInline(TabularInline):
    model = Asset
    extra = 0
    fk_name = "part"
    fields = ("kind", "file", "printer", "is_production", "version", "grams_total", "print_time_min", "parse_error")
    readonly_fields = ("grams_total", "print_time_min", "parse_error")
    verbose_name = "arquivo da peça (3MF fatiado, STL, STEP)"
    verbose_name_plural = "arquivos da peça — suba o .gcode.3mf exportado do Bambu Studio, tipo '3MF fatiado', e marque 'arquivo de produção'"


@admin.register(Part)
class PartAdmin(ModelAdmin):
    list_display = ("name", "product", "default_qty", "color_slot", "has_production_file")
    list_filter = ("product__category",)
    search_fields = ("name", "product__name")
    inlines = [PartAssetInline]
    autocomplete_fields = ("product",)

    def save_related(self, request, form, formsets, change):
        from .workflow import refresh_product, sync_bom_with_parts

        super().save_related(request, form, formsets, change)
        sync_bom_with_parts(form.instance.product)
        refresh_product(form.instance.product)

    @admin.display(description="3MF de produção", boolean=True)
    def has_production_file(self, obj):
        return obj.production_assets().exists()


@admin.register(Component)
class ComponentAdmin(ModelAdmin):
    list_display = ("name", "unit_cost", "stock_qty", "min_stock", "is_packaging", "supplier", "active")
    list_filter = ("is_packaging", "active")
    search_fields = ("name", "supplier")


@admin.register(Asset)
class AssetAdmin(ModelAdmin):
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
