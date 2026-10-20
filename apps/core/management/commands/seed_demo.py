"""
Exemplo da especificação: Luminária Solaris G (cúpula branca, base areia).
Custo esperado R$ 72,19; preços Site 118,90 · Shopee 165,90 · Mercado Livre 177,90.
Os filamentos e componentes criados aqui são marcados como exemplo.
"""

from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.catalog.models import Asset, BOMItem, Category, Color, Component, Part, Product, Variant
from apps.costing.models import Printer
from apps.costing.services import compute_prices, compute_variant_cost
from apps.inventory.models import FilamentSKU, Purchase


class Command(BaseCommand):
    help = "Cria a Luminária Solaris de exemplo com custo e preços calculados."

    def handle(self, *args, **options):
        call_command("seed_base")
        lum = Category.objects.get(code="LUM")
        branco = Color.objects.get(code="BRC")
        areia = Color.objects.get(code="ARE")
        a1 = Printer.objects.get(name="A1 #1")

        fil_branco, created = FilamentSKU.objects.get_or_create(
            brand="Exemplo", material="PLA", color_name="Branco Gesso",
            defaults={"manufacturer": "Exemplo", "color_hex": "#F6F4EF", "color": branco, "notes": "filamento de exemplo"},
        )
        fil_areia, _ = FilamentSKU.objects.get_or_create(
            brand="Exemplo", material="PLA", color_name="Areia",
            defaults={"manufacturer": "Exemplo", "color_hex": "#D9C7A6", "color": areia, "notes": "filamento de exemplo"},
        )
        for fil in (fil_branco, fil_areia):
            if not fil.purchases.exists():
                Purchase.objects.create(filament=fil, quantity=1, grams_total=Decimal("1000"), total_cost=Decimal("95.00"),
                                        supplier="Exemplo", invoice_ref="exemplo").apply()

        kit, _ = Component.objects.get_or_create(name="Kit soquete E27 + cabo com interruptor", defaults={"unit_cost": Decimal("18.00")})
        caixa, _ = Component.objects.get_or_create(name="Caixa, papel e tag", defaults={"unit_cost": Decimal("6.00"), "is_packaging": True})

        product, _ = Product.objects.get_or_create(
            sku_base="TM-LUM-SOL",
            defaults={
                "name": "Luminária Solaris", "category": lum, "status": Product.Status.ACTIVE,
                "short_description": "Canelada, leve e feita sob demanda. A luz passa pelas camadas e aquece o cantinho.",
                "height_mm": 270, "width_mm": 180, "depth_mm": 180, "weight_g": 450, "lead_time_days": 5,
                "tags": "luminária de mesa, 3d, canelada, abajur",
            },
        )
        cupula, _ = Part.objects.get_or_create(product=product, name="Cúpula", defaults={"color_slot": Part.ColorSlot.MAIN})
        base, _ = Part.objects.get_or_create(product=product, name="Base", defaults={"color_slot": Part.ColorSlot.SECONDARY})

        # 3MF "virtuais": no exemplo não há arquivo, só os metadados que o leitor extrairia.
        for part, grams, minutes in ((cupula, Decimal("160.00"), 340), (base, Decimal("110.00"), 200)):
            if not part.production_assets().exists():
                Asset.objects.create(
                    part=part, product=product, kind=Asset.Kind.THREEMF_SLICED, name=f"{part.name.lower()}_A1_v01.gcode.3mf",
                    printer=a1, is_production=True, grams_total=grams, print_time_min=minutes,
                    meta={"filaments": [{"extruder": 1, "grams": float(grams), "type": "PLA"}], "exemplo": True},
                )

        variant, _ = Variant.objects.get_or_create(
            product=product, sku="TM-LUM-SOL-G-BRC",
            defaults={"size": "G", "primary_color": branco, "secondary_color": areia, "main_filament": fil_branco,
                      "secondary_filament": fil_areia, "minutes_post": 10, "minutes_assembly": 10, "minutes_packaging": 5,
                      "is_default": True},
        )
        if not variant.bom_items.exists():
            BOMItem.objects.create(variant=variant, kind=BOMItem.Kind.PART, part=cupula, quantity=1)
            BOMItem.objects.create(variant=variant, kind=BOMItem.Kind.PART, part=base, quantity=1)
            BOMItem.objects.create(variant=variant, kind=BOMItem.Kind.COMPONENT, component=kit, quantity=1)
            BOMItem.objects.create(variant=variant, kind=BOMItem.Kind.COMPONENT, component=caixa, quantity=1)

        variant = Variant.objects.get(pk=variant.pk)  # recarrega os filamentos com o custo médio atualizado
        breakdown, problems = compute_variant_cost(variant)
        prices = compute_prices(variant)
        self.stdout.write(f"Solaris G: custo R$ {breakdown.total}")
        for p in prices:
            self.stdout.write(f"  {p.channel}: sugerido R$ {p.suggested_price} (margem {p.margin_pct}%)")
        for p in problems:
            self.stdout.write(self.style.WARNING(f"  aviso: {p.message}"))
        self.stdout.write(self.style.SUCCESS("Exemplo pronto."))
