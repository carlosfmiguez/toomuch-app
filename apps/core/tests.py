"""Fluxo ponta a ponta com a Solaris de exemplo: seed, custo, preço, compra, produção e telas."""

from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import Asset, Variant
from apps.costing.models import Channel, PriceByChannel
from apps.costing.services import approve_price, compute_prices, latest_prices
from apps.inventory.models import FilamentSKU, Purchase, StockMovement
from apps.production.models import PrintJob, ProductionOrder


class SeedAndFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        cls.variant = Variant.objects.get(sku="TM-LUM-SOL-G-BRC")
        cls.user = get_user_model().objects.create_superuser("carlos", "carlos@example.com", "senha-forte-123")

    def test_demo_cost_and_prices_match_specification(self):
        self.assertEqual(self.variant.cost_cached, D("72.19"))
        prices = {p.channel.name: p for p in latest_prices(self.variant)}
        self.assertEqual(prices["Site"].suggested_price, D("118.90"))
        self.assertEqual(prices["Shopee"].suggested_price, D("165.90"))
        self.assertEqual(prices["Mercado Livre"].suggested_price, D("177.90"))
        self.assertFalse(any(p.below_minimum for p in prices.values()))

    def test_approved_price_survives_recalculation(self):
        row = latest_prices(self.variant)[0]
        approved = approve_price(row, self.user, price=D("129.90"), note="promo")
        self.assertTrue(approved.approved)
        compute_prices(self.variant)
        latest = next(p for p in latest_prices(self.variant) if p.channel_id == row.channel_id)
        self.assertEqual(latest.current_price, D("129.90"))
        self.assertEqual(latest.suggested_price, D("118.90"))

    def test_purchase_updates_weighted_average_cost(self):
        fil = FilamentSKU.objects.get(brand="Exemplo", color_name="Branco Gesso")
        self.assertEqual(fil.avg_cost_per_g, D("0.095000"))
        Purchase.objects.create(filament=fil, quantity=1, grams_total=D("1000"), total_cost=D("80.00"), shipping_cost=D("5.00")).apply()
        fil.refresh_from_db()
        self.assertEqual(fil.stock_g, D("2000.00"))
        self.assertEqual(fil.avg_cost_per_g, D("0.090000"))
        self.assertEqual(StockMovement.objects.filter(filament=fil, kind="purchase").count(), 2)

    def test_production_order_explodes_and_consumes_filament(self):
        order = ProductionOrder.objects.create(variant=self.variant, quantity=2, origin=ProductionOrder.Origin.STOCK)
        jobs = order.explode()
        self.assertEqual(len(jobs), 4)  # 2 peças × 2 unidades
        job = jobs[0]
        fil = job.filament
        before = fil.stock_g
        job.start(operator=self.user)
        job.finish(ok=True, actual_grams=D("165.50"), actual_minutes=350, operator=self.user)
        fil.refresh_from_db()
        self.assertEqual(fil.stock_g, before - D("165.50"))
        self.assertEqual(StockMovement.objects.filter(print_job=job, kind="consumption").count(), 1)
        # falha abre reimpressão e lança refugo
        job2 = jobs[1]
        job2.finish(ok=False, failure_reason="descolou da mesa", operator=self.user)
        self.assertEqual(order.jobs.count(), 5)
        self.assertEqual(StockMovement.objects.filter(print_job=job2, kind="scrap").count(), 1)

    def test_asset_metadata_is_parsed_on_upload(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.files3d.tests import make_3mf

        part = self.variant.product.parts.first()
        asset = Asset.objects.create(
            part=part, product=part.product, kind=Asset.Kind.THREEMF_SLICED,
            file=SimpleUploadedFile("cupula_A1_v02.gcode.3mf", make_3mf()), is_production=False,
        )
        asset.refresh_from_db()
        self.assertEqual(asset.grams_total, D("270.35"))
        self.assertEqual(asset.print_time_min, 540)
        self.assertTrue(asset.thumbnail)
        self.assertEqual(asset.parse_error, "")
        self.assertEqual(len(asset.sha256), 64)

    def test_pages_render(self):
        self.client.login(username="carlos", password="senha-forte-123")
        self.assertRedirects(self.client.get(reverse("core:dashboard")), reverse("admin:index"))
        for name, kwargs in (("admin:index", {}), ("catalog:product_list", {}),
                             ("catalog:product_detail", {"pk": self.variant.product_id}),
                             ("costing:calculator", {}), ("costing:price_list", {}),
                             ("catalog:import_upload", {}), ("production:board", {}),
                             ("publishing:catalog_index", {}), ("tiny:status", {})):
            resp = self.client.get(reverse(name, kwargs=kwargs))
            self.assertEqual(resp.status_code, 200, name)
            self.assertContains(resp, 'id="nav-sidebar"', msg_prefix=name)
        resp = self.client.post(reverse("costing:calculator"), {
            "grams": "270", "cost_per_kg": "95", "hours": "9", "labor_minutes": "25", "components": "18",
            "packaging": "6", "license_cost": "0", "waste_pct": "2", "failure_pct": "5",
            "printer": str(self.variant.product.parts.first().production_asset().printer_id),
        }, HTTP_HX_REQUEST="true")
        self.assertContains(resp, "72,19")
        self.assertContains(resp, "118,90")

    def test_pages_require_login(self):
        resp = self.client.get(reverse("core:dashboard"))
        self.assertEqual(resp.status_code, 302)

    def test_channel_count(self):
        self.assertEqual(Channel.objects.count(), 3)
        self.assertEqual(PriceByChannel.objects.filter(variant=self.variant).count(), 3)


class BadRequestHandlerTests(TestCase):
    def test_staff_sees_reason(self):
        from django.contrib.auth import get_user_model
        from django.core.exceptions import SuspiciousFileOperation
        from django.test import RequestFactory

        from apps.core.views import bad_request

        req = RequestFactory().get("/x/")
        req.user = get_user_model()(username="a", is_staff=True)
        resp = bad_request(req, SuspiciousFileOperation("caminho fora da pasta"))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("SuspiciousFileOperation: caminho fora da pasta", resp.content.decode())
        req.user = get_user_model()(username="b", is_staff=False)
        self.assertNotIn("caminho", bad_request(req, SuspiciousFileOperation("caminho")).content.decode())
