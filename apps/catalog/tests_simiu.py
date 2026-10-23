"""Carga simiu/Thangs simulando o banco de produção (produtos que já existiam)."""

import io

from django.core.management import call_command
from django.test import TestCase

from .models import Category, Collection, Part, Product

EXISTING = {"TM-VAS-BAB": "VAS", "TM-BAN-DRW": "BAN", "TM-MAQ-FLO": "MAQ", "TM-NAT-FLV": "NAT", "TM-NAT-FLK": "NAT",
            "TM-LUM-TWI": "LUM", "TM-BAN-KUT": "BAN", "TM-BAN-SOA": "BAN", "TM-MAQ-CUB": "MAQ", "TM-MAQ-RET": "MAQ",
            "TM-MAQ-TAY": "MAQ", "TM-COZ-PPT": "COZ", "TM-VAS-TAY": "VAS", "TM-BAN-TSA": "BAN", "TM-BAN-TSV": "BAN",
            "TM-CAS-RTP": "CAS", "TM-NAT-EAR": "NAT", "TM-NAT-EDI": "NAT", "TM-NAT-EEL": "NAT", "TM-NAT-EGO": "NAT"}


class SimiuTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_base", verbosity=0)

    def test_skips_elsewhere(self):
        out = io.StringIO()
        call_command("carregar_simiu_thangs", stdout=out)
        self.assertIn("carga pulada", out.getvalue())

    def test_load(self):
        for sku, cat in EXISTING.items():
            Product.objects.create(sku_base=sku, name=sku, category=Category.objects.get(code=cat), short_description="já tinha",
                                   license_author="MakerWorld — designer id 2598485249 (THALOR/...)")
        out = io.StringIO()
        call_command("carregar_simiu_thangs", stdout=out)
        log = out.getvalue()
        self.assertNotIn("ERRO", log, log)
        self.assertIn("fim: 183 criados, 20 atualizados.", log, log)
        self.assertEqual(Product.objects.count(), 203)
        tay = Product.objects.get(sku_base="TM-MAQ-TAY")
        self.assertEqual((tay.short_description, tay.license_author), ("já tinha", "simiu (Thangs)"))
        self.assertTrue(tay.license_url.startswith("https://thangs.com/"))
        self.assertEqual(sorted(p.sku_base for p in Collection.objects.get(name="Linha Taylor").products.all()),
                         ["TM-COZ-PPT", "TM-MAQ-TAY", "TM-VAS-TAY", "TM-VAS-THA"])
        new = Product.objects.get(sku_base="TM-VAS-AIR")
        self.assertEqual((new.origin, new.status, new.license_author), ("unlicensed", "draft", "simiu (Thangs)"))
        self.assertTrue(Part.objects.filter(product=new, name="Peça única").exists())
        self.assertEqual(Collection.objects.get(name="Linha SNOOVA").products.count(), 4)
        out2 = io.StringIO()
        call_command("carregar_simiu_thangs", stdout=out2)
        self.assertIn("fim: 0 criados, 203 atualizados.", out2.getvalue())


class BigReviewTests(TestCase):
    """A revisão de um ZIP com 200+ produtos manda mais de 1000 campos: não pode dar 400."""

    def test_review_with_many_products(self):
        import tempfile
        import zipfile

        from django.contrib.auth import get_user_model
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from django.urls import reverse

        from .tests import jpeg_bytes

        call_command("seed_base", verbosity=0)
        user = get_user_model().objects.create_superuser("carlos", "c@example.com", "senha-forte-123")
        self.client.force_login(user)
        buf = io.BytesIO()
        img = jpeg_bytes()
        with zipfile.ZipFile(buf, "w") as z:
            for n in range(205):
                z.writestr(f"TM-VAS-{n:03d}/foto.jpg", img)
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            r = self.client.post(reverse("catalog:import_upload"), {"zip": SimpleUploadedFile("fotos.zip", buf.getvalue())})
            self.assertEqual(r.status_code, 302, r.content[:500])
            page = self.client.get(reverse("catalog:import_review"))
            data = {}
            for p in page.context["products"]:
                i = p["i"]
                data.update({f"import-{i}": "on", f"name-{i}": p["sp"].name, f"cat-{i}": "VAS", f"sku-{i}": p["sp"].sku_base,
                             f"origin-{i}": "unlicensed", f"author-{i}": "simiu"})
            self.assertGreater(len(data), 1000)
            r = self.client.post(reverse("catalog:import_review"), data)
            self.assertRedirects(r, reverse("catalog:import_progress"), fetch_redirect_response=False)
