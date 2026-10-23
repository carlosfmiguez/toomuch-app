"""3MF fatiado com várias placas: perguntar se são tamanhos (variantes) ou partes (custo somado)."""

import io
import tempfile
import zipfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.files3d import readers
from apps.files3d.tests import make_3mf
from apps.inventory.models import FilamentSKU, Purchase

from . import workflow
from .models import Asset, Category, Component, Part, Product, Variant
from .services import parse_asset

TMP = tempfile.mkdtemp()
MODEL_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate><metadata key="plater_id" value="1"/><metadata key="plater_name" value="100%"/></plate>
  <plate><metadata key="plater_id" value="2"/><metadata key="plater_name" value="70%"/></plate>
</config>"""


def two_plate_3mf() -> bytes:
    src = zipfile.ZipFile(io.BytesIO(make_3mf()))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in src.namelist():
            z.writestr(n, src.read(n))
        z.writestr("Metadata/model_settings.config", MODEL_SETTINGS)
    return buf.getvalue()


def one_of_two_plates_3mf() -> bytes:
    """Exportou só a placa '70%' (2) de um projeto com '100%' e '70%'."""
    from apps.files3d.tests import SLICE_INFO

    only2 = SLICE_INFO.split("<plate>")[0] + "<plate>" + SLICE_INFO.split("<plate>")[2]
    only2 = only2.replace('used_g="110.00"', 'used_g="38.90"').replace('value="110.00"', 'value="38.90"')
    src = zipfile.ZipFile(io.BytesIO(make_3mf()))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in src.namelist():
            z.writestr(n, only2 if n == "Metadata/slice_info.config" else src.read(n))
        z.writestr("Metadata/model_settings.config", MODEL_SETTINGS)
    return buf.getvalue()


@override_settings(MEDIA_ROOT=TMP)
class PlatesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_base", verbosity=0)
        cls.user = get_user_model().objects.create_superuser("carlos", "c@example.com", "senha-forte-123")
        cls.fil = FilamentSKU.objects.create(brand="Bambulab", manufacturer="Bambu Lab", material="PLA LITE", color_name="Branca")
        Purchase.objects.create(filament=cls.fil, quantity=1, grams_total=Decimal("1000"), total_cost=Decimal("90")).apply()

    def setUp(self):
        self.client.force_login(self.user)
        self.product = Product.objects.create(sku_base="TM-REL-ALD", name="Anjo LED canelado", category=Category.objects.get(code="REL"))
        self.part = Part.objects.create(product=self.product, name="Peça única")

    def _upload(self):
        return self.client.post(reverse("catalog:part_upload", args=[self.part.pk]), {
            "file": SimpleUploadedFile("Anjo._70.gcode.3mf", two_plate_3mf()), "printer": ""})

    def test_reader_keeps_plate_names_and_grams(self):
        data = readers.read_sliced_3mf(two_plate_3mf())
        self.assertEqual([(p["name"], p["grams"]) for p in data["plates"]], [("100%", 160.35), ("70%", 110.0)])

    def test_upload_asks_and_sizes_create_variants(self):
        resp = self._upload()
        self.assertRedirects(resp, reverse("catalog:part_plates", args=[self.part.pk]), fetch_redirect_response=False)
        steps = {s.key: s for s in workflow.steps_for(self.product)}
        self.assertFalse(steps["pecas"].done)
        self.assertIn("tem 2 placas (100%, 70%)", " ".join(steps["pecas"].todo))
        page = self.client.get(reverse("catalog:part_plates", args=[self.part.pk]))
        self.assertContains(page, "Tamanhos diferentes do mesmo objeto")
        self.assertContains(page, "160,4 g")
        # sem variante e sem filamento: pede o filamento
        resp = self.client.post(reverse("catalog:part_plates", args=[self.part.pk]), {"mode": "sizes", "label_1": "G", "label_2": "P"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.product.variants.exists())
        resp = self.client.post(reverse("catalog:part_plates", args=[self.part.pk]),
                                {"mode": "sizes", "label_1": "G", "label_2": "P", "filament": self.fil.pk})
        self.assertEqual(resp.status_code, 302)
        parts = {p.name: p for p in self.product.parts.all()}
        self.assertEqual(sorted(parts), ["Anjo LED canelado G", "Anjo LED canelado P"])
        self.assertEqual((parts["Anjo LED canelado G"].plate_index, parts["Anjo LED canelado P"].only_sizes), (1, "P"))
        g = Variant.objects.get(product=self.product, size="G")
        p = Variant.objects.get(product=self.product, size="P")
        self.assertEqual([b.part.name for b in g.bom_items.all()], ["Anjo LED canelado G"])
        self.assertEqual([b.part.name for b in p.bom_items.all()], ["Anjo LED canelado P"])
        lines = lambda v: [ln for ln in v.cost_breakdown["linhas"] if ln["tipo"] == "filamento"]  # noqa: E731
        self.assertEqual(Decimal(lines(g)[0]["gramas"]), Decimal("160.35"))
        self.assertEqual(Decimal(lines(p)[0]["gramas"]), Decimal("110.00"))
        self.assertGreater(g.cost_cached, p.cost_cached)
        self.assertTrue({s.key: s for s in workflow.steps_for(self.product)}["pecas"].done)
        # reler o arquivo não desfaz a decisão
        asset = parts["Anjo LED canelado P"].production_assets().get()
        parse_asset(asset)
        asset.refresh_from_db()
        self.assertEqual(asset.plates_mode, "sizes")
        detail = self.client.get(reverse("catalog:product_detail", args=[self.product.pk]) + "?etapa=pecas")
        self.assertContains(detail, "placa 70%")

    def test_parts_sum_and_existing_variant_becomes_first_size(self):
        self._upload()
        led = Component.objects.create(name="Led 001", unit_cost=Decimal("19.90"))
        v = Variant.objects.create(product=self.product, sku="TM-REL-ALD-U", size="Único", main_filament=self.fil, is_default=True)
        v.ensure_default_bom()
        v.bom_items.create(kind="component", component=led, quantity=1)
        workflow.refresh_variant(Variant.objects.get(pk=v.pk))
        log = workflow.apply_plates(self.part, "sizes", ["G", "P"])
        v.refresh_from_db()
        self.assertEqual((v.size, v.sku), ("G", "TM-REL-ALD-G"))
        newp = Variant.objects.get(product=self.product, size="P")
        self.assertEqual(newp.bom_items.filter(kind="component").get().component, led)
        self.assertTrue(any("variante única virou o tamanho G" in x for x in log))

    def test_parts_mode_sums(self):
        self._upload()
        workflow.apply_plates(self.part, "parts")
        asset = self.part.production_assets().get()
        self.assertEqual(asset.plates_mode, "parts")
        self.assertEqual(self.product.parts.count(), 1)
        self.assertEqual(self.part.print_numbers(asset)[0], Decimal("270.35"))
        self.assertTrue({s.key: s for s in workflow.steps_for(self.product)}["pecas"].done)

    def test_connector_definir_placas(self):
        from apps.connector.tools import TOOLS

        self._upload()
        call = lambda name, **kw: TOOLS[name]["fn"](self.user, "https://x", **kw)  # noqa: E731
        info = call("ver_produto", produto="TM-REL-ALD")
        arq = info["pecas"][0]["arquivos"][0]
        self.assertTrue(arq["decidir_placas"])
        self.assertEqual([p["nome"] for p in arq["placas"]], ["100%", "70%"])
        out = call("definir_placas", produto="TM-REL-ALD", peca_id=self.part.pk, modo="tamanhos",
                   tamanhos=["G", "P"], filamento_principal=str(self.fil.pk))
        self.assertEqual(sorted(v["sku"] for v in out["variantes"]), ["TM-REL-ALD-G", "TM-REL-ALD-P"])

    def test_only_one_plate_sliced_still_asks(self):
        resp = self.client.post(reverse("catalog:part_upload", args=[self.part.pk]), {
            "file": SimpleUploadedFile("Anjo._70.gcode.3mf", one_of_two_plates_3mf()), "printer": ""})
        self.assertRedirects(resp, reverse("catalog:part_plates", args=[self.part.pk]), fetch_redirect_response=False)
        asset = self.part.production_assets().get()
        self.assertEqual(asset.grams_total, Decimal("38.90"))
        self.assertEqual([(p["index"], p["sliced"]) for p in asset.all_plates], [(1, False), (2, True)])
        page = self.client.get(reverse("catalog:part_plates", args=[self.part.pk]))
        self.assertContains(page, "não fatiada neste arquivo")
        log = workflow.apply_plates(self.part, "sizes", ["G", "P"], self.fil)
        self.part.refresh_from_db()
        self.assertEqual((self.part.name, self.part.only_sizes, self.part.plate_index), ("Anjo LED canelado P", "P", None))
        g_part = self.product.parts.get(name="Anjo LED canelado G")
        self.assertFalse(g_part.production_assets().exists())
        self.assertTrue(any("não fatiada" in x for x in log))
        p = Variant.objects.get(product=self.product, size="P")
        g = Variant.objects.get(product=self.product, size="G")
        self.assertEqual(Decimal([ln for ln in p.cost_breakdown["linhas"] if ln["tipo"] == "filamento"][0]["gramas"]), Decimal("38.90"))
        self.assertTrue(any("sem 3MF" in m for m in g.cost_breakdown["problemas"]))
        steps = {s.key: s for s in workflow.steps_for(self.product)}
        self.assertEqual(steps["pecas"].todo, ["Suba o 3MF fatiado da peça “Anjo LED canelado G”."])

    def test_parts_mode_creates_part_for_unsliced_plate(self):
        self.client.post(reverse("catalog:part_upload", args=[self.part.pk]), {
            "file": SimpleUploadedFile("Anjo._70.gcode.3mf", one_of_two_plates_3mf()), "printer": ""})
        workflow.apply_plates(self.part, "parts")
        self.assertEqual(sorted(p.name for p in self.product.parts.all()), ["100%", "Peça única"])
