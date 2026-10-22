"""Fluxo guiado da ficha: criar → 3MF → variante → aprovar preço → foto → ativar."""

import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.costing.services import latest_prices
from apps.files3d.tests import make_3mf
from apps.inventory.models import FilamentSKU, Purchase

from . import workflow
from .models import Category, Color, Product
from .tests import jpeg_bytes

TMP = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TMP)
class FichaFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_base", verbosity=0)
        cls.user = get_user_model().objects.create_superuser("carlos", "c@example.com", "senha-forte-123")
        cls.fil = FilamentSKU.objects.create(brand="Bambu Lab", manufacturer="Bambu Lab", material="PLA", color_name="Branco")
        Purchase.objects.create(filament=cls.fil, quantity=1, grams_total=Decimal("1000"), total_cost=Decimal("89.90")).apply()

    def setUp(self):
        self.client.force_login(self.user)

    def test_full_flow(self):
        rel = Category.objects.get(code="REL")
        resp = self.client.post(reverse("catalog:product_create"), {"name": "Sagrada Família", "category": rel.pk, "sku_base": "", "origin": "own"})
        product = Product.objects.get(name="Sagrada Família")
        self.assertTrue(product.sku_base.startswith("TM-REL-"))
        self.assertRedirects(resp, reverse("catalog:product_detail", args=[product.pk]) + "?etapa=dados", fetch_redirect_response=False)
        part = product.parts.get()
        steps = {s.key: s.done for s in workflow.steps_for(product)}
        self.assertFalse(steps["pecas"])

        # ficha abre e mostra a etapa atual
        resp = self.client.get(reverse("catalog:product_detail", args=[product.pk]))
        self.assertContains(resp, "Etapa 2 de 6", msg_prefix="sem etapa escolhida, abre na próxima pendente")
        self.assertNotContains(resp, "Enviar fotos", msg_prefix="só a etapa atual aparece")

        # 3MF fatiado direto na peça
        f = SimpleUploadedFile("conjunto.gcode.3mf", make_3mf())
        self.client.post(reverse("catalog:part_upload", args=[part.pk]), {"file": f, "printer": ""})
        self.assertTrue(part.production_assets().exists())

        # variante: SKU automático, custo e preços já calculados
        branco = Color.objects.get(code="BRC")
        self.client.post(reverse("catalog:variant_add", args=[product.pk]), {"size": "Único", "primary_color": branco.pk, "main_filament": self.fil.pk})
        v = product.variants.get()
        self.assertEqual(v.sku, f"{product.sku_base}-U-BRC")
        self.assertIsNotNone(v.cost_cached)
        self.assertGreater(v.cost_cached, 0)
        rows = latest_prices(v)
        self.assertTrue(rows and not any(r.approved for r in rows))
        self.assertEqual(len(workflow.pending_approvals()), 1)

        # a fila de aprovação lista a variante
        resp = self.client.get(reverse("costing:approvals"))
        self.assertContains(resp, v.sku)

        # aprova todos, um com preço digitado com vírgula
        site = rows[0]
        data = {f"price_{r.channel_id}": str(r.suggested_price) for r in rows}
        data[f"price_{site.channel_id}"] = "99,90"
        self.client.post(reverse("catalog:variant_prices", args=[v.pk]), data)
        rows = latest_prices(v)
        self.assertTrue(all(r.approved for r in rows))
        self.assertEqual([r.current_price for r in rows if r.channel_id == site.channel_id], [Decimal("99.90")])
        self.assertEqual(workflow.pending_approvals(), [])

        # recalcular não derruba a aprovação nem duplica histórico
        n = v.prices.count()
        workflow.refresh_variant(v)
        self.assertEqual(v.prices.count(), n)
        self.assertTrue(all(r.approved for r in latest_prices(v)))

        # foto e ativação
        self.client.post(reverse("catalog:photo_upload", args=[product.pk]), {"photos": [SimpleUploadedFile("a.jpg", jpeg_bytes())]})
        self.assertTrue(product.assets.filter(kind="photo", is_main=True).exists())
        self.client.post(reverse("catalog:product_status", args=[product.pk]), {"status": "active"})
        product.refresh_from_db()
        self.assertEqual(product.status, "active")
        self.assertTrue(product.ready_for_catalog)

    def test_approve_single_channel_and_edit_variant(self):
        call_command("seed_demo", verbosity=0)
        v = Product.objects.get(sku_base="TM-LUM-SOL").variants.get()
        rows = latest_prices(v)
        one = rows[1]
        self.client.post(reverse("catalog:variant_prices", args=[v.pk]), {"only": one.channel_id, f"price_{one.channel_id}": "150"})
        approved = [r.channel_id for r in latest_prices(v) if r.approved]
        self.assertEqual(approved, [one.channel_id])

        resp = self.client.get(reverse("catalog:variant_edit", args=[v.pk]))
        self.assertEqual(resp.status_code, 200)
        before = v.cost_cached
        data = {
            "sku": v.sku, "size": v.size, "primary_color": v.primary_color_id or "", "secondary_color": v.secondary_color_id or "",
            "main_filament": v.main_filament_id, "secondary_filament": v.secondary_filament_id or "",
            "minutes_post": 40, "minutes_assembly": 0, "minutes_packaging": 5, "stock_ready": 0, "is_default": "on", "action": "save",
        }
        self.client.post(reverse("catalog:variant_edit", args=[v.pk]), data)
        v.refresh_from_db()
        self.assertGreater(v.cost_cached, before, "mais minutos de mão de obra encarecem e o custo é recalculado ao salvar")

    def test_pages_render(self):
        call_command("seed_demo", verbosity=0)
        p = Product.objects.get(sku_base="TM-LUM-SOL")
        for url in (reverse("catalog:product_list"), reverse("catalog:product_create"), reverse("catalog:product_edit", args=[p.pk]),
                    reverse("catalog:product_detail", args=[p.pk]), reverse("costing:approvals")):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        for key in ("dados", "pecas", "variantes", "preco", "fotos", "publicar"):
            resp = self.client.get(reverse("catalog:product_detail", args=[p.pk]) + f"?etapa={key}")
            self.assertEqual(resp.status_code, 200, key)
            self.assertContains(resp, 'aria-current="step"')

    def test_dados_step_saves_and_advances(self):
        call_command("seed_demo", verbosity=0)
        p = Product.objects.get(sku_base="TM-LUM-SOL")
        data = {"name": p.name, "sku_base": p.sku_base, "category": p.category_id, "short_description": "Nova descrição",
                "lead_time_days": 5, "origin": "own", "license_cost_per_unit": "0",
                "next": reverse("catalog:product_detail", args=[p.pk]) + "?etapa=pecas"}
        resp = self.client.post(reverse("catalog:product_edit", args=[p.pk]), data)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].endswith("?etapa=pecas"))


@override_settings(MEDIA_ROOT=TMP)
class CostStaysFreshTests(TestCase):
    """Compra de filamento registrada depois da variante: o custo precisa se atualizar sozinho."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_base", verbosity=0)
        cls.user = get_user_model().objects.create_superuser("carlos", "c@example.com", "senha-forte-123")

    def test_purchase_after_variant_updates_cost(self):
        from .models import Asset, Part, Variant

        rel = Category.objects.get(code="REL")
        p = Product.objects.create(name="Sagrada Família", sku_base="TM-REL-SAG", category=rel)
        part = Part.objects.create(product=p, name="Conjunto")
        Asset.objects.create(part=part, product=p, kind="3mf_sliced", name="x.gcode.3mf", is_production=True,
                             grams_total=Decimal("100"), print_time_min=300, parse_error="")
        fil = FilamentSKU.objects.create(brand="Bambulab", material="PLA LITE", color_name="Branca")
        v = Variant.objects.create(product=p, sku="TM-REL-SAG-U-BRC", main_filament=fil)
        workflow.refresh_variant(v)
        self.assertEqual(v.cost_breakdown["filamento"], "0.00")

        # compra pelo admin, como o usuário faz
        self.client.force_login(self.user)
        self.client.post(reverse("admin:inventory_purchase_add"), {
            "filament": fil.pk, "quantity": "1", "grams_total": "1000", "total_cost": "100.00",
            "shipping_cost": "0.00", "purchased_at": "2026-10-22",
        })
        v.refresh_from_db()
        self.assertEqual(v.cost_breakdown["filamento"], "10.20", "100 g × R$ 0,10/g + 2 % de desperdício")
        self.assertEqual(v.cost_breakdown["problemas"], [])

        # corrigir o valor da compra também recalcula
        purchase = fil.purchases.get()
        self.client.post(reverse("admin:inventory_purchase_change", args=[purchase.pk]), {
            "filament": fil.pk, "quantity": "1", "grams_total": "1000", "total_cost": "200.00",
            "shipping_cost": "0.00", "purchased_at": "2026-10-22",
        })
        v.refresh_from_db()
        self.assertEqual(v.cost_breakdown["filamento"], "20.40")


@override_settings(MEDIA_ROOT=TMP)
class ReusePartTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        cls.user = get_user_model().objects.create_superuser("carlos", "c@example.com", "senha-forte-123")

    def setUp(self):
        from .models import Variant

        self.client.force_login(self.user)
        self.sol = Product.objects.get(sku_base="TM-LUM-SOL")
        self.base = self.sol.parts.get(name="Base")
        self.client.post(reverse("catalog:product_create"), {"name": "Luminária Spin", "category": self.sol.category_id, "origin": "own"})
        self.spin = Product.objects.get(name="Luminária Spin")
        Variant.objects.create(product=self.spin, sku="TM-LUM-SPI-U", main_filament=FilamentSKU.objects.first(),
                               secondary_filament=FilamentSKU.objects.last())

    def test_library_listed(self):
        resp = self.client.get(reverse("catalog:product_detail", args=[self.spin.pk]) + "?etapa=pecas")
        self.assertContains(resp, "Usar peça já cadastrada")
        self.assertContains(resp, "Luminária Solaris")

    def test_share_part(self):
        self.client.post(reverse("catalog:part_reuse", args=[self.spin.pk]), {"part": self.base.pk, "mode": "share"})
        self.assertIn(self.base, self.spin.all_parts())
        self.assertFalse(self.spin.parts.filter(name="Peça única").exists(), "a peça vazia criada junto some")
        v = self.spin.variants.get()
        self.assertEqual([b.part_id for b in v.bom_items.filter(kind="part")], [self.base.pk])
        self.assertGreater(v.cost_breakdown["filamento"], "0")
        # não dá para apagar no produto de origem enquanto está compartilhada
        self.client.post(reverse("catalog:part_delete", args=[self.base.pk]))
        self.assertTrue(type(self.base).objects.filter(pk=self.base.pk).exists())
        # desvincular tira só do Spin
        self.client.post(reverse("catalog:part_unlink", args=[self.spin.pk, self.base.pk]))
        self.assertNotIn(self.base, self.spin.all_parts())
        self.assertFalse(v.bom_items.filter(part=self.base).exists())
        self.assertTrue(self.sol.variants.get().bom_items.filter(part=self.base).exists())

    def test_copy_part(self):
        self.client.post(reverse("catalog:part_reuse", args=[self.spin.pk]), {"part": self.base.pk, "mode": "copy"})
        copy = self.spin.parts.get(name="Base")
        self.assertNotEqual(copy.pk, self.base.pk)
        asset = copy.production_asset()
        self.assertEqual(asset.grams_total, self.base.production_asset().grams_total)
        self.assertTrue(workflow.steps_for(self.spin)[1].done)

    def test_part_from_loose_file(self):
        from .models import Asset

        loose = Asset.objects.create(kind="3mf_sliced", name="pes_tripe.gcode.3mf", grams_total=Decimal("40"), print_time_min=90,
                                     parsed_at="2026-10-22T10:00Z")
        self.client.post(reverse("catalog:part_from_file", args=[self.spin.pk]), {"asset": loose.pk})
        loose.refresh_from_db()
        self.assertEqual(loose.part.product_id, self.spin.pk)
        self.assertTrue(loose.is_production)
