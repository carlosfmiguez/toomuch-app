"""Peça com gramas/tempo estimados (sem 3MF) e peça restrita a um tamanho de variante."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.inventory.models import FilamentSKU, Purchase

from . import workflow
from .models import Category, Part, Product, Variant


class EstimateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_base", verbosity=0)
        cls.user = get_user_model().objects.create_superuser("carlos", "c@example.com", "senha-forte-123")
        cls.fil = FilamentSKU.objects.create(brand="STLFLIX", manufacturer="STLFLIX", material="PLA Matte", color_name="Verde")
        Purchase.objects.create(filament=cls.fil, quantity=1, grams_total=Decimal("1000"), total_cost=Decimal("79")).apply()
        cls.fil2 = FilamentSKU.objects.create(brand="Sunlu", manufacturer="Sunlu", material="PLA Silk", color_name="Dourado")
        Purchase.objects.create(filament=cls.fil2, quantity=1, grams_total=Decimal("1000"), total_cost=Decimal("140")).apply()

    def _cruz(self):
        p = Product.objects.create(sku_base="TM-PAS-CRZ", name="Cruz de Páscoa", category=Category.objects.get(code="PAS"))
        Part.objects.create(product=p, name="Cruz P", est_grams=Decimal("47"), est_minutes=101, only_sizes="P")
        Part.objects.create(product=p, name="Cruz G", est_grams=Decimal("185"), est_minutes=338, only_sizes="G")
        Part.objects.create(product=p, name="Ovo", est_grams=Decimal("6"), est_minutes=40, color_slot=Part.ColorSlot.SECONDARY)
        return p

    def test_cost_uses_estimate_and_size_parts(self):
        p = self._cruz()
        vp = Variant.objects.create(product=p, sku="TM-PAS-CRZ-P", size="P", main_filament=self.fil, secondary_filament=self.fil2)
        vg = Variant.objects.create(product=p, sku="TM-PAS-CRZ-G", size="G", main_filament=self.fil, secondary_filament=self.fil2)
        for v in (vp, vg):
            workflow.refresh_variant(Variant.objects.get(pk=v.pk))  # filamento fresco do banco (custo médio veio da compra)
        vp.refresh_from_db()
        vg.refresh_from_db()
        self.assertEqual(sorted(b.part.name for b in vp.bom_items.all()), ["Cruz P", "Ovo"])
        self.assertEqual(sorted(b.part.name for b in vg.bom_items.all()), ["Cruz G", "Ovo"])
        self.assertGreater(vp.cost_cached, 0)
        self.assertGreater(vg.cost_cached, vp.cost_cached)
        self.assertTrue(any("estimativa manual" in m for m in vp.cost_breakdown["problemas"]))
        # 47 g a R$ 0,079/g + 6 g a R$ 0,14/g, antes de desperdício
        self.assertEqual(Decimal(vp.cost_breakdown["filamento"]).quantize(Decimal("1")), Decimal("5"))
        steps = {s.key: s for s in workflow.steps_for(p)}
        self.assertFalse(steps["pecas"].done)
        self.assertIn("estimativa de 47 g / 1h41", " ".join(steps["pecas"].todo))
        self.assertTrue(steps["variantes"].done)
        # nova peça sem tamanho entra nas duas; sync não devolve a peça do outro tamanho
        Part.objects.create(product=p, name="Coelho", est_grams=Decimal("5"))
        workflow.sync_bom_with_parts(p)
        self.assertEqual(sorted(b.part.name for b in vp.bom_items.all()), ["Coelho", "Cruz P", "Ovo"])

    def test_part_form_partial_update_keeps_estimate(self):
        p = self._cruz()
        part = p.parts.get(name="Cruz P")
        self.client.force_login(self.user)
        url = reverse("catalog:part_update", args=[part.pk])
        self.client.post(url, {f"p{part.pk}-name": "Cruz pequena", f"p{part.pk}-default_qty": 1, f"p{part.pk}-color_slot": "main"})
        part.refresh_from_db()
        self.assertEqual(part.name, "Cruz pequena")
        self.assertEqual(part.est_grams, Decimal("47"))
        self.assertEqual(part.only_sizes, "P")
        self.client.post(url, {f"p{part.pk}-est_grams": "50", f"p{part.pk}-est_minutes": "100", f"p{part.pk}-only_sizes": ""})
        part.refresh_from_db()
        self.assertEqual((part.name, part.est_grams, part.est_minutes, part.only_sizes), ("Cruz pequena", Decimal("50"), 100, ""))
        resp = self.client.get(reverse("catalog:product_detail", args=[p.pk]) + "?etapa=pecas")
        self.assertContains(resp, "o custo usa a estimativa")

    def test_connector_full_product(self):
        from apps.connector.tools import TOOLS

        call = lambda name, **kw: TOOLS[name]["fn"](self.user, "https://x", **kw)  # noqa: E731
        p = Product.objects.create(sku_base="TM-PAS-CRZ", name="Cruz de Páscoa", category=Category.objects.get(code="PAS"))
        placeholder = Part.objects.create(product=p, name="Peça única")
        from .models import Asset
        stl = Asset.objects.create(product=p, part=placeholder, kind=Asset.Kind.STL, name="cruz.stl")
        spec = dict(produto="TM-PAS-CRZ", nome="Cruz de Páscoa", altura_mm=200, colecoes=["Páscoa 2027"], pecas=[
            {"nome": "Cruz", "gramas": 47, "minutos": 101, "tamanhos": "P"},
            {"nome": "Cruz G", "gramas": 185, "minutos": 338, "tamanhos": "G"},
            {"nome": "Ovo", "gramas": 6, "minutos": 40, "slot": "secundaria"},
        ], variantes=[
            {"tamanho": "P", "filamento_principal": str(self.fil.pk), "filamento_secundario": str(self.fil2.pk)},
            {"tamanho": "G", "filamento_principal": str(self.fil.pk), "filamento_secundario": str(self.fil2.pk)},
        ])
        out = call("cadastrar_produto_completo", **spec)
        self.assertFalse(out["criado"])
        self.assertIn("peça provisória 'Peça única' substituída (1 arquivo(s) movidos para a biblioteca do produto)", out["alteracoes"])
        stl.refresh_from_db()
        self.assertIsNone(stl.part_id)
        self.assertEqual(sorted(v["sku"] for v in out["variantes"]), ["TM-PAS-CRZ-G", "TM-PAS-CRZ-P"])
        self.assertTrue(all(v["custo"] and v["sugerido"] for v in out["variantes"]), out)
        by_sku = {v["sku"]: v for v in out["variantes"]}
        self.assertEqual(by_sku["TM-PAS-CRZ-P"]["composicao"], ["1 × Cruz", "1 × Ovo"])
        # rodar de novo não duplica nada
        out2 = call("cadastrar_produto_completo", **spec)
        self.assertEqual(out2["alteracoes"], [])
        p.refresh_from_db()
        self.assertEqual((p.parts.count(), p.variants.count(), p.height_mm), (3, 2, 200))
        self.assertEqual([c.name for c in p.collections.all()], ["Páscoa 2027"])
        # produto novo nasce sem licença
        novo = call("cadastrar_produto_completo", nome="Vaso Encanto", categoria="VAS",
                    pecas=[{"nome": "Vaso", "gramas": 92, "minutos": 53}],
                    variantes=[{"tamanho": "Único", "filamento_principal": str(self.fil.pk)}])
        self.assertTrue(novo["criado"])
        self.assertEqual(Product.objects.get(sku_base=novo["sku_base"]).origin, "unlicensed")
