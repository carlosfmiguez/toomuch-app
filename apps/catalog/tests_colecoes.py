"""Coleções (linha de design): cadastro na ficha, filtro interno, portal público, conector e importação."""

import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.costing.services import approve_price, latest_prices
from apps.files3d.tests import make_3mf

from . import importer
from .models import Asset, Category, Collection, Product, Variant
from .tests import jpeg_bytes

TMP = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TMP)
class CollectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        cls.user = get_user_model().objects.create_superuser("carlos", "c@example.com", "senha-forte-123")
        cls.solaris = Product.objects.get(sku_base="TM-LUM-SOL")
        cls.vaso = Product.objects.create(sku_base="TM-VAS-TAY", name="Vaso Taylor", category=Category.objects.get(code="VAS"))

    def setUp(self):
        cache.clear()
        self.client.force_login(self.user)

    # --- sistema -------------------------------------------------------------
    def _post_dados(self, product, **extra):
        data = {"name": product.name, "sku_base": product.sku_base, "category": product.category_id,
                "lead_time_days": 5, "origin": "own", "license_cost_per_unit": "0"}
        data.update(extra)
        return self.client.post(reverse("catalog:product_edit", args=[product.pk]), data)

    def test_create_and_pick_collection_in_dados_step(self):
        resp = self.client.get(reverse("catalog:product_detail", args=[self.solaris.pk]) + "?etapa=dados")
        self.assertContains(resp, "Nenhuma coleção ainda")
        resp = self._post_dados(self.solaris, new_collections="Linha Taylor, Natal 2026")
        self.assertEqual(resp.status_code, 302, resp.content[:2000])
        self.assertEqual(sorted(c.name for c in self.solaris.collections.all()), ["Linha Taylor", "Natal 2026"])
        taylor = Collection.objects.get(name="Linha Taylor")
        self.assertEqual(taylor.slug, "linha-taylor")
        # outra peça, de outra categoria, entra na mesma linha marcando a caixa; nome repetido não duplica
        self._post_dados(self.vaso, collections=[taylor.pk], new_collections="linha taylor")
        self.assertEqual(list(self.vaso.collections.all()), [taylor])
        self.assertEqual(Collection.objects.count(), 2)
        # desmarcar tudo tira a peça da coleção
        self._post_dados(self.vaso)
        self.assertFalse(self.vaso.collections.exists())

    def test_product_list_filter(self):
        taylor = Collection.objects.create(name="Linha Taylor")
        self.vaso.collections.add(taylor)
        resp = self.client.get(reverse("catalog:product_list"), {"colecao": "linha-taylor"})
        self.assertContains(resp, "Vaso Taylor")
        self.assertNotContains(resp, "Luminária Solaris")
        self.assertContains(resp, "Todas as coleções")

    def test_admin_collection_sets_products(self):
        resp = self.client.post(reverse("admin:catalog_collection_add"), {
            "name": "Linha Onda", "slug": "", "description": "Curvas.", "show_on_site": "on", "order": 0,
            "products": [self.solaris.pk, self.vaso.pk],
        })
        self.assertEqual(resp.status_code, 302, resp.content[:3000])
        col = Collection.objects.get(name="Linha Onda")
        self.assertEqual(col.products.count(), 2)
        self.assertEqual(self.client.get(reverse("admin:catalog_collection_changelist")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:catalog_collection_change", args=[col.pk])).status_code, 200)

    # --- portal público ---------------------------------------------------------
    def _make_solaris_public(self):
        variant = Variant.objects.get(sku="TM-LUM-SOL-G-BRC")
        Asset.objects.create(product=self.solaris, kind=Asset.Kind.PHOTO, is_main=True,
                             file=SimpleUploadedFile("s.jpg", jpeg_bytes(), content_type="image/jpeg"))
        for row in latest_prices(variant):
            approve_price(row, self.user, price=row.suggested_price)
        cache.clear()

    def test_public_collection_pages(self):
        self._make_solaris_public()
        taylor = Collection.objects.create(name="Linha Taylor", description="Ondas suaves em toda a casa.")
        hidden = Collection.objects.create(name="Rascunho interno", show_on_site=False)
        self.solaris.collections.add(taylor, hidden)
        self.client.logout()

        home = self.client.get(reverse("public:home"))
        self.assertContains(home, "Coleções")
        self.assertContains(home, reverse("public:collection", args=["linha-taylor"]))
        self.assertNotContains(home, "Rascunho interno")

        page = self.client.get(reverse("public:collection", args=["linha-taylor"]))
        self.assertContains(page, "Ondas suaves em toda a casa.")
        self.assertContains(page, "Luminária Solaris")
        self.assertEqual(self.client.get(reverse("public:collection", args=[hidden.slug])).status_code, 404)

        cat = self.client.get(reverse("public:category", args=["lum"]))
        self.assertContains(cat, "Filtrar por coleção")
        self.assertContains(cat, "Luminária Solaris")
        other = Collection.objects.create(name="Outra")
        cat = self.client.get(reverse("public:category", args=["lum"]), {"colecao": other.slug})
        self.assertNotContains(cat, "Luminária Solaris")

        prod = self.client.get(reverse("public:product", args=["tm-lum-sol"]))
        self.assertContains(prod, "coleção Linha Taylor")
        self.assertNotContains(prod, "Rascunho interno")

    # --- conector ---------------------------------------------------------------
    def test_connector_tools(self):
        from apps.connector.tools import TOOLS, ToolError

        base = "https://app.example.com"
        call = lambda name, **kw: TOOLS[name]["fn"](self.user, base, **kw)  # noqa: E731
        out = call("gerenciar_colecao", acao="criar", colecao="Linha Taylor", descricao="Curvas.",
                   produtos=["TM-VAS-TAY"])
        self.assertEqual(out["produtos"], ["TM-VAS-TAY"])
        self.assertTrue(out["portal"].endswith("/colecao/linha-taylor/"))
        call("gerenciar_colecao", acao="atualizar", colecao="linha-taylor", adicionar_produtos=["TM-LUM-SOL"])
        self.assertEqual(Collection.objects.get().products.count(), 2)
        with self.assertRaises(ToolError):
            call("gerenciar_colecao", acao="criar", colecao="linha taylor")

        up = call("atualizar_produto", produto="TM-LUM-SOL", colecoes=["Linha Taylor", "Natal 2026"])
        self.assertIn("colecoes", up["alterado"])
        self.assertEqual(sorted(up["colecoes"]), ["Linha Taylor", "Natal 2026"])
        lst = call("listar_produtos", colecao="Natal 2026")
        self.assertEqual([p["sku_base"] for p in lst["produtos"]], ["TM-LUM-SOL"])
        self.assertEqual(len(call("listar_cadastros")["colecoes"]), 2)
        call("atualizar_produto", produto="TM-LUM-SOL", colecoes=[])
        self.assertFalse(self.solaris.collections.exists())
        novo = call("criar_produto", nome="Porta-maquiagem Taylor", categoria="VAS", colecoes=["Linha Taylor"])
        self.assertEqual(novo["colecoes"], ["Linha Taylor"])
        gone = call("gerenciar_colecao", acao="excluir", colecao="Natal 2026")
        self.assertEqual(gone["excluida"], "Natal 2026")

    # --- importação -------------------------------------------------------------
    def test_import_manifest_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VAS-ONDA Vaso onda").mkdir()
            (root / "VAS-ONDA Vaso onda" / "vaso.3mf").write_bytes(make_3mf())
            (root / "produtos.csv").write_text(
                "Pasta;Nome;Categoria;SKU-base;Origem;Coleção\n"
                "VAS-ONDA Vaso onda;Vaso Onda;VAS;TM-VAS-OND;own;Linha Onda | Natal 2026\n", encoding="utf-8")
            report = importer.apply(root, user=self.user)
        self.assertFalse(report.errors, report.errors)
        p = Product.objects.get(sku_base="TM-VAS-OND")
        self.assertEqual(sorted(c.name for c in p.collections.all()), ["Linha Onda", "Natal 2026"])


@override_settings(MEDIA_ROOT=TMP)
class PhotoWithoutThumbnailTests(TestCase):
    """Foto cuja miniatura não foi gerada (servidor caiu no envio) não pode derrubar a página pública."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        cls.user = get_user_model().objects.create_superuser("carlos", "c@example.com", "senha-forte-123")

    def test_public_page_and_repair_command(self):
        sol = Product.objects.get(sku_base="TM-LUM-SOL")
        variant = Variant.objects.get(sku="TM-LUM-SOL-G-BRC")
        photo = Asset.objects.create(product=sol, kind=Asset.Kind.PHOTO, is_main=True,
                                     file=SimpleUploadedFile("s.jpg", jpeg_bytes(), content_type="image/jpeg"))
        Asset.objects.filter(pk=photo.pk).update(thumbnail="")
        for row in latest_prices(variant):
            approve_price(row, self.user, price=row.suggested_price)
        cache.clear()
        resp = self.client.get(reverse("public:product", args=["TM-LUM-SOL"]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, Asset.objects.get(pk=photo.pk).file.url)
        call_command("refazer_miniaturas", verbosity=0)
        self.assertTrue(Asset.objects.get(pk=photo.pk).thumbnail)


@override_settings(MEDIA_ROOT=TMP)
class ProjectAndSlicedSamePartTests(TestCase):
    """Projeto (.3mf) e fatiado (.gcode.3mf) do mesmo modelo viram UMA peça, não duas (o custo dobraria)."""

    def test_same_part(self):
        call_command("seed_demo", verbosity=0)
        user = get_user_model().objects.create_superuser("carlos", "c@example.com", "senha-forte-123")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Rena").mkdir()
            (root / "Rena" / "rena.3mf").write_bytes(make_3mf(with_slice_info=False))
            (root / "Rena" / "rena.gcode.3mf").write_bytes(make_3mf())
            (root / "Arvore").mkdir()
            (root / "Arvore" / "Arvore G.gcode.3mf").write_bytes(make_3mf())
            (root / "Arvore" / "Arvore M.gcode.3mf").write_bytes(make_3mf(with_gcode=True))
            importer.apply(root, user=user)
        rena = Product.objects.get(name="Rena")
        self.assertEqual(list(rena.parts.values_list("name", flat=True)), ["Peça única"])
        self.assertEqual(rena.assets.exclude(part=None).count(), 2)
        arv = Product.objects.get(name="Arvore")
        self.assertEqual(sorted(arv.parts.values_list("name", flat=True)), ["Arvore G", "Arvore M"])


@override_settings(MEDIA_ROOT=TMP)
class ColorVariantAndWeightTests(TestCase):
    """Variante de cor reaproveita a peça (mesmo fatiado); peso sai do fatiado para a ficha."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        cls.user = get_user_model().objects.create_superuser("carlos", "c@example.com", "senha-forte-123")

    def setUp(self):
        self.client.force_login(self.user)

    def test_color_variant_copies_parts_and_components(self):
        from .models import BOMItem, Color, Component, Part

        base = Variant.objects.get(sku="TM-LUM-SOL-G-BRC")
        prod = base.product
        comp = Component.objects.create(name="Led Vela", unit_cost=3)
        BOMItem.objects.create(variant=base, kind=BOMItem.Kind.COMPONENT, component=comp, quantity=1)
        Part.objects.filter(product=prod).update(only_sizes="G")  # peça limitada ao tamanho G
        base.minutes_assembly = 7
        base.save()
        color = Color.objects.exclude(pk=base.primary_color_id).first()
        resp = self.client.post(reverse("catalog:variant_add", args=[prod.pk]), {
            "base": base.pk, "size": "", "primary_color": color.pk, "main_filament": base.main_filament_id})
        self.assertEqual(resp.status_code, 302)
        new = prod.variants.exclude(pk__in=Variant.objects.filter(product=prod, created_at__lte=base.created_at)).latest("created_at")
        self.assertEqual(new.size, base.size)
        self.assertEqual(
            sorted(new.bom_items.filter(kind="part").values_list("part_id", flat=True)),
            sorted(base.bom_items.filter(kind="part").values_list("part_id", flat=True)))
        self.assertTrue(new.bom_items.filter(component=comp).exists())
        self.assertEqual(new.minutes_assembly, 7)
        self.assertGreater(new.cost_cached, 0)

        # outra cor num tamanho novo: a peça passa a valer também nele e não some
        resp = self.client.post(reverse("catalog:variant_add", args=[prod.pk]), {
            "base": base.pk, "size": "P", "primary_color": color.pk, "main_filament": base.main_filament_id})
        p_var = prod.variants.get(size="P")
        self.assertTrue(p_var.bom_items.filter(kind="part").exists())
        from .workflow import sync_bom_with_parts
        sync_bom_with_parts(prod)
        self.assertTrue(p_var.bom_items.filter(kind="part").exists())

    def test_add_part_in_variant_editor(self):
        from .models import BOMItem

        v = Variant.objects.get(sku="TM-LUM-SOL-G-BRC")
        item = v.bom_items.filter(kind="part").first()
        self.client.post(reverse("catalog:variant_edit", args=[v.pk]), {"action": f"remove_part:{item.pk}"})
        self.assertFalse(v.bom_items.filter(part=item.part).exists())
        page = self.client.get(reverse("catalog:variant_edit", args=[v.pk]))
        self.assertContains(page, "Adicionar peça")
        self.client.post(reverse("catalog:variant_edit", args=[v.pk]), {"action": "add_part", "part": item.part_id})
        self.assertTrue(BOMItem.objects.filter(variant=v, part=item.part).exists())

    def test_weight_from_sliced_file(self):
        from . import dims

        prod = Product.objects.get(sku_base="TM-LUM-SOL")
        rows, best = dims.weights(prod)
        self.assertTrue(rows)
        self.assertGreater(best, 0)
        Product.objects.filter(pk=prod.pk).update(weight_g=None)
        page = self.client.get(reverse("catalog:product_detail", args=[prod.pk]) + "?etapa=dados")
        self.assertContains(page, "data-use-weight")
        self.assertContains(page, f'value="{best}"')
