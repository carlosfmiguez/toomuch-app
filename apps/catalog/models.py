"""
Catálogo: produto, variantes, peças impressas, composição (BOM), componentes e arquivos.

A Variante é a unidade vendável (SKU completo); a Peça impressa é a unidade que
sai da impressora; o Item de composição liga uma variante às suas peças,
componentes comprados e embalagem.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class Category(models.Model):
    name = models.CharField("nome", max_length=80, unique=True)
    code = models.CharField("código no SKU", max_length=3, unique=True, help_text="3 letras, ex.: LUM")
    order = models.PositiveSmallIntegerField("ordem", default=0)

    class Meta:
        verbose_name = "categoria"
        verbose_name_plural = "categorias"
        ordering = ["order", "name"]

    def __str__(self) -> str:
        return self.name


class Color(models.Model):
    """Apelido de 3 letras usado no SKU, ligado ao nome oficial de cor do fabricante."""

    code = models.CharField("código no SKU", max_length=3, unique=True, help_text="3 letras, ex.: BRC")
    name = models.CharField("nome na Toomuch", max_length=60)
    hex = models.CharField("hex", max_length=7, blank=True, help_text="#RRGGBB para swatches")

    class Meta:
        verbose_name = "cor"
        verbose_name_plural = "cores"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class Collection(models.Model):
    """
    Linha de design que agrupa peças de funções e categorias diferentes
    (ex.: Linha Taylor = vaso + porta-maquiagem + luminária). Serve de filtro
    no sistema e de página/agrupador no portal público.
    """

    name = models.CharField("nome", max_length=80, unique=True)
    slug = models.SlugField("endereço no portal", max_length=80, unique=True, blank=True,
                            help_text="Gerado do nome se ficar em branco (ex.: linha-taylor).")
    description = models.TextField("descrição", blank=True, help_text="Aparece no topo da página da coleção no portal.")
    show_on_site = models.BooleanField("mostrar no portal", default=True)
    order = models.PositiveSmallIntegerField("ordem", default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "coleção"
        verbose_name_plural = "coleções"
        ordering = ["order", "name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify

            base = slugify(self.name)[:70] or "colecao"
            slug, n = base, 2
            while Collection.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)

    @classmethod
    def get_or_create_by_name(cls, name: str) -> "Collection":
        name = " ".join((name or "").split())
        found = cls.objects.filter(name__iexact=name).first()
        return found or cls.objects.create(name=name)


class Product(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Rascunho"
        ACTIVE = "active", "Ativo"
        SEASONAL = "seasonal", "Sazonal"
        DISCONTINUED = "discontinued", "Descontinuado"

    class Origin(models.TextChoices):
        OWN = "own", "Autoral"
        LICENSED = "licensed", "Terceiros — licença comercial"
        UNLICENSED = "unlicensed", "Terceiros — sem licença de venda"

    sku_base = models.CharField("SKU-base", max_length=40, unique=True, help_text="Ex.: TM-LUM-SOL")
    name = models.CharField("nome", max_length=120)
    category = models.ForeignKey(Category, verbose_name="categoria", on_delete=models.PROTECT, related_name="products")
    collections = models.ManyToManyField(
        Collection, verbose_name="coleções", blank=True, related_name="products",
        help_text="Linha de design. Uma peça pode estar em mais de uma (ex.: Linha Taylor e Natal 2026).",
    )
    short_description = models.CharField("descrição curta", max_length=200, blank=True)
    long_description = models.TextField("descrição longa", blank=True)
    tags = models.CharField("tags SEO", max_length=300, blank=True, help_text="separadas por vírgula")
    height_mm = models.PositiveIntegerField("altura (mm)", null=True, blank=True)
    width_mm = models.PositiveIntegerField("largura (mm)", null=True, blank=True)
    depth_mm = models.PositiveIntegerField("profundidade (mm)", null=True, blank=True)
    weight_g = models.PositiveIntegerField("peso total (g)", null=True, blank=True)
    lead_time_days = models.PositiveSmallIntegerField("prazo de produção (dias úteis)", default=5)
    status = models.CharField("status", max_length=20, choices=Status.choices, default=Status.DRAFT)

    origin = models.CharField("origem do design", max_length=20, choices=Origin.choices, default=Origin.OWN)
    license_author = models.CharField("autor / fonte", max_length=120, blank=True)
    license_url = models.URLField("link da licença ou do modelo", blank=True)
    license_terms = models.CharField("termos", max_length=200, blank=True)
    license_cost_per_unit = models.DecimalField(
        "custo de licença por unidade (R$)", max_digits=10, decimal_places=2, default=Decimal("0.00"),
        help_text="Só quando a licença cobra por peça vendida; licença paga uma vez fica fora do custo unitário.",
    )
    license_valid_until = models.DateField("licença válida até", null=True, blank=True)
    license_attribution_required = models.BooleanField("exige atribuição", default=False)

    shared_parts = models.ManyToManyField(
        "Part", verbose_name="peças compartilhadas de outros produtos", blank=True, related_name="shared_in",
        help_text="Peça de outro produto usada também neste (mesmo 3MF; trocar o arquivo vale para todos).",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "produto"
        verbose_name_plural = "produtos"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.sku_base})"

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("catalog:product_detail", args=[self.pk])

    # --- regras ---------------------------------------------------------
    @property
    def license_is_valid(self) -> bool:
        if self.origin == self.Origin.OWN:
            return True
        if self.origin == self.Origin.UNLICENSED:
            return False
        if self.license_valid_until and self.license_valid_until < timezone.localdate():
            return False
        return True

    @property
    def main_photo(self):
        return self.assets.filter(kind=Asset.Kind.PHOTO).order_by("-is_main", "order", "id").first()

    def all_parts(self) -> list["Part"]:
        """Peças próprias + peças compartilhadas de outros produtos."""
        own = list(self.parts.all())
        if not self.pk:
            return own
        return own + [p for p in self.shared_parts.select_related("product") if p.product_id != self.pk]

    def catalog_checklist(self) -> list[tuple[str, bool]]:
        """Itens do 'pronto para catálogo' e se cada um está ok."""
        parts = self.all_parts()
        has_sliced = bool(parts) and all(p.production_assets().exists() for p in parts)
        variants = list(self.variants.all())
        bom_ok = bool(variants) and all(v.bom_items.exists() for v in variants)
        photos_ok = self.assets.filter(kind=Asset.Kind.PHOTO).count() >= 1
        price_ok = bool(variants) and all(
            v.prices.filter(approved_at__isnull=False).exists() for v in variants
        )
        return [
            ("3MF fatiado em todas as peças", has_sliced),
            ("Composição (BOM) completa", bom_ok),
            ("Fotos mínimas", photos_ok),
            ("Licença de venda válida", self.license_is_valid),
            ("Preço aprovado", price_ok),
        ]

    @property
    def ready_for_catalog(self) -> bool:
        return all(ok for _, ok in self.catalog_checklist())


class Part(models.Model):
    """Peça impressa: um objeto que sai da impressora. Um produto pode ter várias."""

    class ColorSlot(models.TextChoices):
        MAIN = "main", "Cor principal"
        SECONDARY = "secondary", "Cor secundária"

    product = models.ForeignKey(Product, verbose_name="produto", on_delete=models.CASCADE, related_name="parts")
    name = models.CharField("nome da peça", max_length=80, help_text="Ex.: cúpula, base, pés")
    default_qty = models.PositiveSmallIntegerField("quantidade por produto", default=1)
    color_slot = models.CharField("slot de cor", max_length=12, choices=ColorSlot.choices, default=ColorSlot.MAIN)
    waste_pct_override = models.DecimalField(
        "desperdício (%) desta peça", max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Vazio usa o parâmetro global.",
    )
    failure_pct_override = models.DecimalField(
        "taxa de falha (%) desta peça", max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Vazio usa o parâmetro global.",
    )
    notes = models.CharField("observações", max_length=200, blank=True)

    class Meta:
        verbose_name = "peça impressa"
        verbose_name_plural = "peças impressas"
        ordering = ["product", "id"]

    def __str__(self) -> str:
        return f"{self.product.name} · {self.name}"

    def production_assets(self):
        return self.assets.filter(kind=Asset.Kind.THREEMF_SLICED, is_production=True)

    def production_asset(self, printer=None):
        """O 3MF fatiado de produção para a impressora dada (ou o único/primeiro)."""
        qs = self.production_assets()
        if printer is not None:
            specific = qs.filter(printer=printer).first()
            if specific:
                return specific
            generic = qs.filter(printer__isnull=True).first()
            if generic:
                return generic
        return qs.first()


class Variant(models.Model):
    """Unidade vendável: tamanho + cores. O que vai ao Tiny e ao catálogo."""

    product = models.ForeignKey(Product, verbose_name="produto", on_delete=models.CASCADE, related_name="variants")
    sku = models.CharField("SKU", max_length=60, unique=True, help_text="Ex.: TM-LUM-SOL-G-BRC")
    size = models.CharField("tamanho", max_length=20, default="Único", help_text="P, G, Único…")
    primary_color = models.ForeignKey(
        Color, verbose_name="cor principal", on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )
    secondary_color = models.ForeignKey(
        Color, verbose_name="cor secundária", on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )
    main_filament = models.ForeignKey(
        "inventory.FilamentSKU", verbose_name="filamento do slot principal", on_delete=models.PROTECT,
        related_name="variants_as_main", null=True, blank=True,
    )
    secondary_filament = models.ForeignKey(
        "inventory.FilamentSKU", verbose_name="filamento do slot secundário", on_delete=models.PROTECT,
        related_name="variants_as_secondary", null=True, blank=True,
    )
    minutes_post = models.PositiveSmallIntegerField("pós-processamento (min)", default=10)
    minutes_assembly = models.PositiveSmallIntegerField("montagem (min)", default=0)
    minutes_packaging = models.PositiveSmallIntegerField("embalagem (min)", default=5)
    stock_ready = models.PositiveIntegerField("estoque pronto", default=0)
    is_default = models.BooleanField("variante padrão", default=False)

    cost_cached = models.DecimalField("custo calculado (R$)", max_digits=10, decimal_places=2, null=True, blank=True)
    cost_cached_at = models.DateTimeField("custo calculado em", null=True, blank=True)
    cost_breakdown = models.JSONField("detalhe do custo", default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "variante"
        verbose_name_plural = "variantes"
        ordering = ["product", "sku"]

    def __str__(self) -> str:
        return self.sku

    @property
    def label(self) -> str:
        bits = [self.product.name, self.size]
        if self.primary_color:
            bits.append(self.primary_color.name)
        return " · ".join(bits)

    @property
    def labor_minutes(self) -> int:
        return self.minutes_post + self.minutes_assembly + self.minutes_packaging

    def filament_for_slot(self, slot: str):
        if slot == Part.ColorSlot.SECONDARY and self.secondary_filament_id:
            return self.secondary_filament
        return self.main_filament

    def ensure_default_bom(self) -> int:
        """Sem composição ainda? Entra uma linha por peça impressa do produto (quantidade = padrão da peça)."""
        if self.bom_items.exists():
            return 0
        created = 0
        for part in self.product.all_parts():
            BOMItem.objects.create(variant=self, kind=BOMItem.Kind.PART, part=part, quantity=Decimal(part.default_qty))
            created += 1
        return created


class Component(models.Model):
    """Insumo comprado: kit elétrico, lâmpada, caixa, tag, fita."""

    name = models.CharField("nome", max_length=120)
    supplier = models.CharField("fornecedor", max_length=120, blank=True)
    unit = models.CharField("unidade", max_length=20, default="un")
    unit_cost = models.DecimalField("custo unitário (R$)", max_digits=10, decimal_places=2, default=Decimal("0.00"))
    stock_qty = models.DecimalField("saldo", max_digits=10, decimal_places=2, default=Decimal("0"))
    min_stock = models.DecimalField("estoque mínimo", max_digits=10, decimal_places=2, default=Decimal("0"))
    is_packaging = models.BooleanField("é embalagem", default=False)
    url = models.URLField("link de compra", blank=True)
    active = models.BooleanField("ativo", default=True)

    class Meta:
        verbose_name = "componente / insumo"
        verbose_name_plural = "componentes e insumos"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class BOMItem(models.Model):
    """Uma linha da composição de uma variante."""

    class Kind(models.TextChoices):
        PART = "part", "Peça impressa"
        COMPONENT = "component", "Componente comprado"

    variant = models.ForeignKey(Variant, verbose_name="variante", on_delete=models.CASCADE, related_name="bom_items")
    kind = models.CharField("tipo", max_length=12, choices=Kind.choices, default=Kind.PART)
    part = models.ForeignKey(Part, verbose_name="peça", on_delete=models.PROTECT, null=True, blank=True, related_name="bom_items")
    component = models.ForeignKey(
        Component, verbose_name="componente", on_delete=models.PROTECT, null=True, blank=True, related_name="bom_items"
    )
    quantity = models.DecimalField("quantidade", max_digits=8, decimal_places=2, default=Decimal("1"))
    color_slot = models.CharField(
        "slot de cor", max_length=12, choices=Part.ColorSlot.choices, blank=True,
        help_text="Vazio usa o slot da peça.",
    )

    class Meta:
        verbose_name = "item de composição"
        verbose_name_plural = "itens de composição"

    def __str__(self) -> str:
        target = self.part or self.component
        return f"{self.quantity} × {target}"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.kind == self.Kind.PART and not self.part_id:
            raise ValidationError({"part": "Escolha a peça impressa."})
        if self.kind == self.Kind.COMPONENT and not self.component_id:
            raise ValidationError({"component": "Escolha o componente."})

    @property
    def effective_slot(self) -> str:
        if self.color_slot:
            return self.color_slot
        if self.part_id:
            return self.part.color_slot
        return Part.ColorSlot.MAIN


def asset_upload_to(instance: "Asset", filename: str) -> str:
    base = instance.product.sku_base if instance.product_id else "geral"
    return f"arquivos/{base}/{filename}"


class Asset(models.Model):
    """Arquivo: 3MF, STL, STEP, F3D, foto, render, PDF. A fonte única, com versão e hash."""

    class Kind(models.TextChoices):
        THREEMF_SLICED = "3mf_sliced", "3MF fatiado (.gcode.3mf)"
        THREEMF_PROJECT = "3mf_project", "3MF de projeto"
        STL = "stl", "STL"
        STEP = "step", "STEP"
        F3D = "f3d", "F3D (Fusion)"
        PHOTO = "photo", "Foto"
        RENDER = "render", "Render"
        PDF = "pdf", "PDF"
        OTHER = "other", "Outro"

    product = models.ForeignKey(Product, verbose_name="produto", on_delete=models.CASCADE, related_name="assets", null=True, blank=True)
    part = models.ForeignKey(Part, verbose_name="peça", on_delete=models.CASCADE, related_name="assets", null=True, blank=True)
    variant = models.ForeignKey(Variant, verbose_name="variante", on_delete=models.CASCADE, related_name="assets", null=True, blank=True)
    kind = models.CharField("tipo", max_length=16, choices=Kind.choices)
    file = models.FileField("arquivo", upload_to=asset_upload_to, blank=True, max_length=300)
    name = models.CharField("nome", max_length=200, blank=True)
    version = models.PositiveSmallIntegerField("versão", default=1)
    sha256 = models.CharField("hash SHA-256", max_length=64, blank=True, db_index=True)
    size_bytes = models.BigIntegerField("tamanho (bytes)", default=0)
    printer = models.ForeignKey(
        "costing.Printer", verbose_name="impressora-alvo", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="assets", help_text="Só para 3MF fatiado: para qual impressora foi fatiado.",
    )
    is_production = models.BooleanField("arquivo de produção", default=False)
    is_main = models.BooleanField("foto principal", default=False)
    order = models.PositiveSmallIntegerField("ordem", default=0)
    thumbnail = models.ImageField("thumbnail", upload_to="thumbs/", blank=True, max_length=300)

    # Metadados lidos do arquivo (slice_info.config, STL etc.)
    grams_total = models.DecimalField("gramas (total)", max_digits=10, decimal_places=2, null=True, blank=True)
    print_time_min = models.PositiveIntegerField("tempo de impressão (min)", null=True, blank=True)
    meta = models.JSONField("metadados", default=dict, blank=True)
    parsed_at = models.DateTimeField("lido em", null=True, blank=True)
    parse_error = models.CharField("erro de leitura", max_length=300, blank=True)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="enviado por", on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "arquivo"
        verbose_name_plural = "arquivos"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name or (self.file.name if self.file else f"arquivo #{self.pk}")

    def save(self, *args, **kwargs):
        if self.file and not self.name:
            self.name = self.file.name.rsplit("/", 1)[-1]
        if self.file and not self.sha256:
            self.sha256, self.size_bytes = self._digest()
        super().save(*args, **kwargs)

    def _digest(self) -> tuple[str, int]:
        h = hashlib.sha256()
        size = 0
        f = self.file
        f.open("rb")
        try:
            for chunk in f.chunks():
                h.update(chunk)
                size += len(chunk)
        finally:
            try:
                f.seek(0)
            except Exception:  # noqa: BLE001 - storages sem seek
                pass
        return h.hexdigest(), size

    @property
    def grams_by_filament(self) -> list[dict]:
        """[{"extruder": 1, "grams": 160.3, "type": "PLA", "color": "#FFFFFF"}, ...]"""
        return list(self.meta.get("filaments", []))

    @property
    def print_hours(self) -> Decimal:
        return (Decimal(self.print_time_min or 0) / Decimal(60)).quantize(Decimal("0.0001"))
