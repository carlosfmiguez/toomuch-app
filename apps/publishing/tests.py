"""Catálogo PDF/HTML e exportação para marketplaces a partir da Solaris de exemplo."""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import Asset, Product, Variant
from apps.costing.models import Channel
from apps.costing.services import approve_price, latest_prices

from . import catalog
from .exports import build_export, build_title, write_csv, write_xlsx
from .models import Publication


def tiny_jpeg() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 50), (233, 185, 143)).save(buf, format="JPEG")
    return buf.getvalue()


class CatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        cls.product = Product.objects.get(sku_base="TM-LUM-SOL")
        cls.variant = Variant.objects.get(sku="TM-LUM-SOL-G-BRC")
        cls.user = get_user_model().objects.create_superuser("carlos", "c@example.com", "senha-forte-123")
        cls.site = Channel.objects.get(name="Site")

    def make_ready(self):
        Asset.objects.create(product=self.product, kind=Asset.Kind.PHOTO, is_main=True,
                             file=SimpleUploadedFile("solaris.jpg", tiny_jpeg(), content_type="image/jpeg"))
        for row in latest_prices(self.variant):
            approve_price(row, self.user, price=row.suggested_price)
        self.product.refresh_from_db()

    def test_product_is_not_ready_without_photo_and_approval(self):
        self.assertFalse(self.product.ready_for_catalog)
        doc = catalog.build_catalog(self.site, only_ready=True)
        self.assertEqual(doc.pages, [])
        doc_all = catalog.build_catalog(self.site, only_ready=False)
        self.assertEqual(len(doc_all.pages), 1)

    def test_catalog_pages_and_pdf(self):
        self.make_ready()
        self.assertTrue(self.product.ready_for_catalog)
        doc = catalog.build_catalog(self.site)
        self.assertEqual(len(doc.pages), 1)
        page = doc.pages[0]
        self.assertEqual(page.page_number, 3)
        self.assertEqual(str(page.price_from), "118.90")
        self.assertTrue(page.price_approved)
        self.assertEqual(doc.categories[0][1], 3)
        self.assertIsNotNone(doc.cover)
        html = catalog.render_catalog_html(doc, for_pdf=False)
        self.assertIn("Luminária Solaris", html)
        self.assertIn("118,90", html)
        pdf = catalog.render_catalog_pdf(doc)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(catalog.register_publications(doc, self.user), 1)
        self.assertEqual(Publication.objects.filter(destination="pdf").count(), 1)

    def test_views(self):
        self.make_ready()
        self.client.login(username="carlos", password="senha-forte-123")
        self.assertContains(self.client.get(reverse("publishing:catalog_index")), "Luminária Solaris")
        self.assertContains(self.client.get(reverse("publishing:catalog_html")), "Sumário")
        resp = self.client.get(reverse("publishing:catalog_pdf"), {"canal": self.site.pk})
        self.assertEqual(resp["Content-Type"], "application/pdf")
        resp = self.client.get(reverse("publishing:product_page_pdf", args=[self.product.pk]))
        self.assertEqual(resp["Content-Type"], "application/pdf")

    def test_marketplace_exports(self):
        self.make_ready()
        ml = Channel.objects.get(name="Mercado Livre")
        rows, columns = build_export("ml", ml, base_url="http://testserver/")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Preço"], "177.90")
        self.assertLessEqual(len(rows[0]["Título"]), 60)
        self.assertIn("http://testserver/media/", rows[0]["Fotos"])
        self.assertIn("DIMENSÕES", rows[0]["Descrição"])
        csv_bytes = write_csv(rows, columns)
        self.assertIn(b"TM-LUM-SOL-G-BRC", csv_bytes)
        xlsx = write_xlsx(rows, columns)
        self.assertTrue(xlsx.startswith(b"PK"))
        shopee_rows, _ = build_export("shopee", Channel.objects.get(name="Shopee"))
        self.assertEqual(shopee_rows[0]["Preço"], "165.90")
        self.assertEqual(shopee_rows[0]["Peso (kg)"], "0.450")

    def test_title_limit(self):
        self.product.name = "Luminária Solaris com um nome enorme que passa do limite do marketplace"
        title = build_title(self.product, self.variant, 60)
        self.assertLessEqual(len(title), 60)
        self.assertTrue(title.startswith("Luminárias 3D"))

    def test_export_views(self):
        self.make_ready()
        self.client.login(username="carlos", password="senha-forte-123")
        resp = self.client.get(reverse("publishing:export_marketplace", args=["ml"]), {"canal": Channel.objects.get(name="Mercado Livre").pk, "formato": "xlsx"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp["Content-Type"])
        resp = self.client.get(reverse("publishing:export_marketplace", args=["shopee"]), {"canal": Channel.objects.get(name="Shopee").pk})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])
