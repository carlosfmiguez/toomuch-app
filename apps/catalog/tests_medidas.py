"""Medidas sugeridas a partir do 3MF: transformações (escala por placa), placa certa e preenchimento na ficha."""

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
from apps.inventory.models import FilamentSKU, Purchase

from . import dims, workflow
from .models import Asset, Category, Part, Product

TMP = tempfile.mkdtemp()
NS = 'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"'
# caixa 10 × 20 × 30 mm
CUBE = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x in (0, 10) for y in (0, 20) for z in (0, 30))
OBJECT_FILE = f'<?xml version="1.0"?><model {NS}><resources><object id="1" type="model"><mesh><vertices>{CUBE}</vertices></mesh></object></resources><build/></model>'
MAIN = f"""<?xml version="1.0"?><model {NS}><resources>
<object id="4" type="model"><components><component p:path="/3D/Objects/object_1.model" objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/></components></object>
<object id="5" type="model"><components><component p:path="/3D/Objects/object_1.model" objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/></components></object>
</resources><build>
<item objectid="4" transform="1 0 0 0 1 0 0 0 1 100 100 0"/>
<item objectid="5" transform="0.5 0 0 0 0.5 0 0 0 0.5 400 100 0"/>
</build></model>"""
SETTINGS = """<?xml version="1.0"?><config>
<plate><metadata key="plater_id" value="1"/><metadata key="plater_name" value="100%"/><model_instance><metadata key="object_id" value="4"/></model_instance></plate>
<plate><metadata key="plater_id" value="2"/><metadata key="plater_name" value="50%"/><model_instance><metadata key="object_id" value="5"/></model_instance></plate>
</config>"""
SLICE_ONLY_2 = """<?xml version="1.0"?><config><plate><metadata key="index" value="2"/><metadata key="prediction" value="3600"/>
<metadata key="weight" value="12.5"/><object identify_id="1" name="anjo" skipped="false"/>
<filament id="1" type="PLA" color="#FFFFFF" used_m="4" used_g="12.5"/></plate></config>"""


def sliced_half_plate(with_mesh=True, sliced=True, bambu_json=False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if with_mesh:
            z.writestr("3D/3dmodel.model", MAIN)
            z.writestr("3D/Objects/object_1.model", OBJECT_FILE)
        z.writestr("Metadata/model_settings.config", SETTINGS)
        if sliced:
            z.writestr("Metadata/slice_info.config", SLICE_ONLY_2)
        if bambu_json:  # como o Bambu grava no .gcode.3mf: área na mesa + cabeçalho do G-code
            z.writestr("Metadata/plate_2.json", '{"bbox_all": [120.0, 95.0, 130.0, 100.0], "bbox_objects": []}')
            z.writestr("Metadata/plate_2.gcode", "; HEADER_BLOCK_START\n; max_z_height: 15.00\n; HEADER_BLOCK_END\nG28\n")
    return buf.getvalue()


class GeometryReaderTests(TestCase):
    def test_transforms_and_plates(self):
        geo = readers.read_geometry(io.BytesIO(sliced_half_plate()))
        self.assertEqual(geo["plates"]["1"], {"x": 10.0, "y": 20.0, "z": 30.0})
        self.assertEqual(geo["plates"]["2"], {"x": 5.0, "y": 10.0, "z": 15.0})
        self.assertEqual(geo["overall"]["x"], 305.0)  # os dois itens juntos: por isso a medida é por placa


@override_settings(MEDIA_ROOT=TMP)
class SuggestionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_base", verbosity=0)
        cls.user = get_user_model().objects.create_superuser("carlos", "c@example.com", "senha-forte-123")
        cls.fil = FilamentSKU.objects.create(brand="Bambulab", manufacturer="Bambu Lab", material="PLA LITE", color_name="Branca")
        Purchase.objects.create(filament=cls.fil, quantity=1, grams_total=Decimal("1000"), total_cost=Decimal("90")).apply()

    def setUp(self):
        self.client.force_login(self.user)
        self.product = Product.objects.create(sku_base="TM-REL-ALD", name="Anjo LED", category=Category.objects.get(code="REL"))
        self.part = Part.objects.create(product=self.product, name="Peça única")

    def test_single_part_prefills_dados_and_sizes_measure_each_plate(self):
        self.client.post(reverse("catalog:part_upload", args=[self.part.pk]), {
            "file": SimpleUploadedFile("anjo_50.gcode.3mf", sliced_half_plate()), "printer": ""})
        rows, best = dims.suggestions(self.product, compute=True)
        self.assertIsNone(best)  # placas ainda sem decisão: cada uma só como referência
        self.assertEqual(sorted((d.label, d.height) for d in rows), [("placa 100%", 30), ("placa 50%", 15)])
        self.assertNotIn("o 3MF sugere", " ".join(workflow.steps_for(self.product)[0].todo))
        page = self.client.get(reverse("catalog:product_detail", args=[self.product.pk]) + "?etapa=dados")
        self.assertContains(page, "placa 50%")
        self.assertNotContains(page, "Medidas preenchidas a partir do 3MF")
        # tamanhos: G (placa não fatiada) é medida pela geometria do projeto dentro do 3MF da P
        workflow.apply_plates(self.part, "sizes", ["G", "P"], self.fil)
        rows, best = dims.suggestions(self.product, compute=True)
        self.assertEqual(sorted((d.label, d.height) for d in rows), [("G", 30), ("P", 15)])
        self.assertEqual((best.label, best.height, best.width, best.depth), ("G", 30, 20, 10))
        self.assertIn("o 3MF sugere 30 × 20 × 10 mm", " ".join(workflow.steps_for(self.product)[0].todo))
        page = self.client.get(reverse("catalog:product_detail", args=[self.product.pk]) + "?etapa=dados")
        self.assertContains(page, 'name="height_mm" value="30"')
        # medida já preenchida não é sobrescrita
        self.product.height_mm = 31
        self.product.save()
        page = self.client.get(reverse("catalog:product_detail", args=[self.product.pk]) + "?etapa=dados")
        self.assertContains(page, 'name="height_mm" value="31"')

    def test_stl_and_multi_part_only_reference(self):
        Asset.objects.create(product=self.product, part=self.part, kind=Asset.Kind.STL, name="corpo.stl",
                             meta={"bbox_mm": {"x": 40, "y": 60, "z": 120}}, parsed_at="2026-01-01T00:00Z")
        rows, best = dims.suggestions(self.product)
        self.assertEqual((best.height, best.width, best.depth), (120, 60, 40))
        base = Part.objects.create(product=self.product, name="Base")
        Asset.objects.create(product=self.product, part=base, kind=Asset.Kind.STL, name="base.stl",
                             meta={"bbox_mm": {"x": 80, "y": 80, "z": 20}}, parsed_at="2026-01-01T00:00Z")
        rows, best = dims.suggestions(self.product)
        self.assertEqual(len(rows), 2)
        self.assertIsNone(best)  # várias partes: a medida montada não sai dos arquivos
        page = self.client.get(reverse("catalog:product_detail", args=[self.product.pk]) + "?etapa=dados")
        self.assertContains(page, "use como referência")

    def test_sliced_without_mesh_uses_project_3mf_from_library(self):
        Asset.objects.create(product=self.product, part=None, kind=Asset.Kind.THREEMF_PROJECT,
                             file=SimpleUploadedFile("anjo.3mf", sliced_half_plate(sliced=False)))
        self.client.post(reverse("catalog:part_upload", args=[self.part.pk]), {
            "file": SimpleUploadedFile("anjo_50.gcode.3mf", sliced_half_plate(with_mesh=False)), "printer": ""})
        sliced = self.part.production_assets().get()
        workflow.apply_plates(self.part, "sizes", ["G", "P"], self.fil)
        rows, best = dims.suggestions(self.product, compute=True)
        self.assertEqual(sorted((d.label, d.height) for d in rows), [("G", 30), ("P", 15)])
        sliced.refresh_from_db()
        self.assertEqual(sliced.meta["geometry"]["erro"], "o arquivo não traz a malha 3D")

    def test_bambu_sliced_without_mesh_uses_plate_json_and_gcode_height(self):
        geo = readers.read_geometry(io.BytesIO(sliced_half_plate(with_mesh=False, bambu_json=True)))
        self.assertEqual(geo["plates"]["2"], {"x": 10.0, "y": 5.0, "z": 15.0})
        self.client.post(reverse("catalog:part_upload", args=[self.part.pk]), {
            "file": SimpleUploadedFile("anjo_50.gcode.3mf", sliced_half_plate(with_mesh=False, bambu_json=True)), "printer": ""})
        workflow.apply_plates(self.part, "sizes", ["G", "P"], self.fil)
        rows, best = dims.suggestions(self.product, compute=True)
        self.assertEqual([(d.label, d.height, d.width, d.depth) for d in rows if d.label == "P"], [("P", 15, 10, 5)])
        # tentativa antiga que só olhou a malha é refeita
        asset = self.part.production_assets().get()
        asset.meta = {**asset.meta, "geometry": {"v": 2, "overall": None, "plates": {}, "erro": "o arquivo não traz a malha 3D"}}
        asset.save(update_fields=["meta"])
        self.assertEqual(dims.ensure_geometry(asset)["plates"]["2"]["z"], 15.0)
