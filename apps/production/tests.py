"""Quadro de produção: iniciar, concluir com pesagem, falha e avanço da ordem até 'pronto'."""

from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import Variant
from apps.costing.models import Printer
from apps.inventory.models import StockMovement

from .models import PrintJob, ProductionOrder


class BoardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        cls.variant = Variant.objects.get(sku="TM-LUM-SOL-G-BRC")
        cls.user = get_user_model().objects.create_superuser("carlos", "c@example.com", "senha-forte-123")

    def setUp(self):
        self.client.login(username="carlos", password="senha-forte-123")
        self.order = ProductionOrder.objects.create(variant=self.variant, quantity=1)
        self.jobs = self.order.explode()

    def test_board_lists_jobs_per_printer(self):
        resp = self.client.get(reverse("production:board"))
        self.assertContains(resp, "A1 #1")
        self.assertContains(resp, "Cúpula #1")

    def test_full_flow_to_ready(self):
        printer = Printer.objects.get(name="A1 #2")
        job_cupula, job_base = self.jobs
        fil = job_cupula.filament
        stock_before = fil.stock_g

        resp = self.client.post(reverse("production:job_start", args=[job_cupula.pk]), {"printer": printer.pk})
        self.assertRedirects(resp, reverse("production:board"))
        job_cupula.refresh_from_db()
        self.assertEqual(job_cupula.result, PrintJob.Result.PRINTING)
        self.assertEqual(job_cupula.printer, printer)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, ProductionOrder.Status.PRINTING)

        self.client.post(reverse("production:job_finish", args=[job_cupula.pk]), {"grams": "162,5", "minutes": "345"})
        job_cupula.refresh_from_db()
        self.assertEqual(job_cupula.result, PrintJob.Result.OK)
        self.assertEqual(job_cupula.actual_grams, D("162.5"))
        fil.refresh_from_db()
        self.assertEqual(fil.stock_g, stock_before - D("162.5"))

        self.client.post(reverse("production:job_start", args=[job_base.pk]), {"printer": printer.pk})
        self.client.post(reverse("production:job_finish", args=[job_base.pk]), {})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, ProductionOrder.Status.POST)

        self.client.post(reverse("production:order_advance", args=[self.order.pk]), {"minutes": "12"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, ProductionOrder.Status.ASSEMBLY)
        self.assertEqual(self.order.minutes_post_actual, 12)
        self.client.post(reverse("production:order_advance", args=[self.order.pk]), {"minutes": "9"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, ProductionOrder.Status.READY)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_ready, 1)

    def test_failure_opens_reprint_and_scrap(self):
        job = self.jobs[0]
        self.client.post(reverse("production:job_start", args=[job.pk]), {"printer": Printer.objects.first().pk})
        self.client.post(reverse("production:job_fail", args=[job.pk]), {"reason": "descolou", "grams": "40"})
        job.refresh_from_db()
        self.assertEqual(job.result, PrintJob.Result.FAILED)
        self.assertEqual(self.order.jobs.count(), 3)
        scrap = StockMovement.objects.get(print_job=job, kind="scrap")
        self.assertEqual(scrap.grams, D("-40"))
