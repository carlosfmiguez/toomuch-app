"""Carga da planilha V2.2 simulando o banco de produção (ids dos filamentos e produtos existentes)."""

import io
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from apps.catalog.management.commands.carregar_planilha_v22 import FINGERPRINT
from apps.inventory.models import FilamentSKU, Purchase

from .models import Asset, Category, Part, Product

EXISTING = {"TM-MAQ-RET": "MAQ", "TM-MAQ-CUB": "MAQ", "TM-MAQ-TAY": "MAQ", "TM-NAT-SIN": "NAT", "TM-PAS-CJA": "PAS",
            "TM-PAS-CCA": "PAS", "TM-PAS-OPT": "PAS", "TM-PAS-OTM": "PAS", "TM-PAS-OOD": "PAS", "TM-PAS-COD": "PAS",
            "TM-PAS-CRZ": "PAS"}


class PlanilhaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_base", verbosity=0)

    def test_skips_on_other_database(self):
        out = io.StringIO()
        call_command("carregar_planilha_v22", stdout=out)
        self.assertIn("carga pulada", out.getvalue())

    def test_full_load_like_production(self):
        for pk, (brand, material) in FINGERPRINT.items():
            f = FilamentSKU.objects.create(pk=pk, brand=brand, manufacturer=brand, material=f"PLA {material}", color_name=f"c{pk}")
            Purchase.objects.create(filament=f, quantity=1, grams_total=Decimal("1000"), total_cost=Decimal("90")).apply()
        for sku, cat in EXISTING.items():
            p = Product.objects.create(sku_base=sku, name=sku, category=Category.objects.get(code=cat))
            ph = Part.objects.create(product=p, name="Peça única")
            Asset.objects.create(product=p, part=ph, kind=Asset.Kind.STL, name=f"{sku}.stl")
        out = io.StringIO()
        call_command("carregar_planilha_v22", stdout=out)
        log = out.getvalue()
        self.assertIn("novo TM-NAT-A11", log)
        self.assertNotIn("ERRO", log, log)
        self.assertIn("fim: 37 criados, 11 atualizados.", log, log)
        crz = Product.objects.get(sku_base="TM-PAS-CRZ")
        self.assertFalse(crz.parts.filter(name="Peça única").exists())
        self.assertEqual(crz.assets.filter(part__isnull=True).count(), 1)
        p_var = crz.variants.get(size="P")
        self.assertEqual(sorted(b.part.name for b in p_var.bom_items.all()), ["Coelho P", "Cruz P", "Ovo P"])
        self.assertGreater(p_var.cost_cached, 0)
        # todo produto carregado tem variante com custo
        loaded = Product.objects.exclude(sku_base__in=[]).filter(variants__isnull=False).distinct()
        self.assertEqual(loaded.count(), 48)
        self.assertFalse([v.sku for p in loaded for v in p.variants.all() if not v.cost_cached])
        alder = Product.objects.get(name="Luminária Alder")
        self.assertEqual(alder.origin, "unlicensed")
        self.assertEqual(alder.variants.get().secondary_filament_id, 10)
        self.assertEqual(sorted(p.name for p in Product.objects.filter(collections__name="Linha Taylor")),
                         ["TM-MAQ-TAY", "Vaso Taylor"])
        # rodar de novo não cria nada
        out2 = io.StringIO()
        call_command("carregar_planilha_v22", stdout=out2)
        self.assertIn("fim: 0 criados, 48 atualizados.", out2.getvalue())
        self.assertEqual(Product.objects.count(), 48)
