"""Formulários da ficha do produto (telas próprias, fora do admin)."""

from __future__ import annotations

from django import forms

from apps.costing.models import Printer
from apps.inventory.models import FilamentSKU

from .models import BOMItem, Collection, Color, Component, Part, Product, Variant


class FilamentChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj: FilamentSKU) -> str:
        cost = f"R$ {obj.avg_cost_per_kg}/kg" if obj.avg_cost_per_g > 0 else "sem custo — registre uma compra"
        return f"{obj} · {cost}"


def filament_field(label: str, required: bool = False) -> FilamentChoiceField:
    return FilamentChoiceField(
        label=label, required=required, empty_label="— escolha —",
        queryset=FilamentSKU.objects.filter(active=True).order_by("brand", "material", "color_name"),
    )


class ProductCreateForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ("name", "category", "sku_base", "origin")
        labels = {"sku_base": "SKU-base"}
        help_texts = {"sku_base": "Deixe em branco para gerar (ex.: TM-LUM-SOL)."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sku_base"].required = False
        self.fields["name"].widget.attrs.update({"autofocus": True, "placeholder": "Ex.: Sagrada Família"})

    def clean_sku_base(self):
        sku = (self.cleaned_data.get("sku_base") or "").strip().upper()
        if sku and Product.objects.filter(sku_base=sku).exists():
            raise forms.ValidationError("Já existe um produto com este SKU-base.")
        return sku


class CollectionsMixin(forms.ModelForm):
    """Coleções existentes (caixas de marcar) + campo para criar novas na hora."""

    new_collections = forms.CharField(
        label="nova coleção", required=False, max_length=200,
        help_text="Digite para criar e já incluir; separe várias por vírgula.",
        widget=forms.TextInput(attrs={"placeholder": "Ex.: Linha Taylor"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "collections" in self.fields:
            self.fields["collections"].widget = forms.CheckboxSelectMultiple(attrs={"class": "tm-chipcheck"})
            self.fields["collections"].queryset = Collection.objects.all()
            self.fields["collections"].help_text = "Linha de design que agrupa peças de funções diferentes no portal e nos filtros."

    def save(self, commit=True):
        obj = super().save(commit=commit)
        names = [n.strip() for n in (self.cleaned_data.get("new_collections") or "").split(",") if n.strip()]
        if names:
            extra = [Collection.get_or_create_by_name(n) for n in names]
            if commit:
                obj.collections.add(*extra)
            else:
                old = self.save_m2m

                def save_m2m():
                    old()
                    obj.collections.add(*extra)
                self.save_m2m = save_m2m
        return obj


class ProductDataForm(CollectionsMixin):
    class Meta:
        model = Product
        fields = (
            "name", "sku_base", "category", "collections", "short_description", "long_description", "tags",
            "height_mm", "width_mm", "depth_mm", "weight_g", "lead_time_days",
            "origin", "license_author", "license_url", "license_terms", "license_cost_per_unit",
            "license_valid_until", "license_attribution_required",
        )
        widgets = {
            "long_description": forms.Textarea(attrs={"rows": 5}),
            "license_valid_until": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }


class PartForm(forms.ModelForm):
    class Meta:
        model = Part
        fields = ("name", "default_qty", "color_slot")
        labels = {"default_qty": "qtd."}


class PartFileForm(forms.Form):
    file = forms.FileField(label="3MF fatiado (.gcode.3mf)")
    printer = forms.ModelChoiceField(
        label="impressora", queryset=Printer.objects.filter(status=Printer.Status.ACTIVE), required=False,
        empty_label="qualquer",
    )

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith(".3mf"):
            raise forms.ValidationError("Envie o .gcode.3mf exportado do Bambu Studio (Arquivo → Exportar → arquivo fatiado da placa).")
        return f


class VariantCreateForm(forms.Form):
    size = forms.CharField(label="tamanho", initial="Único", max_length=20)
    primary_color = forms.ModelChoiceField(label="cor principal", queryset=Color.objects.all(), required=False, empty_label="—")
    main_filament = filament_field("filamento da cor principal", required=True)
    secondary_color = forms.ModelChoiceField(label="cor secundária", queryset=Color.objects.all(), required=False, empty_label="—")
    secondary_filament = filament_field("filamento da cor secundária")


class VariantForm(forms.ModelForm):
    main_filament = filament_field("filamento da cor principal")
    secondary_filament = filament_field("filamento da cor secundária")

    class Meta:
        model = Variant
        fields = (
            "sku", "size", "primary_color", "secondary_color", "main_filament", "secondary_filament",
            "minutes_post", "minutes_assembly", "minutes_packaging", "stock_ready", "is_default",
        )


class ComponentLineForm(forms.Form):
    component = forms.ModelChoiceField(
        label="componente", queryset=Component.objects.filter(active=True), empty_label="— escolha —",
    )
    quantity = forms.DecimalField(label="qtd.", min_value=0, decimal_places=2, initial=1)


class PhotoUploadForm(forms.Form):
    pass  # arquivos vêm de request.FILES.getlist("photos")


class BOMQuantityForm(forms.ModelForm):
    class Meta:
        model = BOMItem
        fields = ("quantity",)
