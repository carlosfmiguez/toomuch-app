"""
Integração Tiny com uma API simulada: OAuth (código → tokens, refresh), envio de
produtos (criar/atualizar/preço/estoque, idempotência), importação de pedidos
aprovados virando ordens de produção (uma vez por pedido) e a tela de status.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal as D
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Asset, Product, Variant
from apps.costing.models import Channel
from apps.costing.services import approve_price, latest_prices
from apps.production.models import ProductionOrder

from . import client as tiny_client
from . import services
from .models import TinyConnection, TinyOrderSync, TinyProductLink, TinySyncLog


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.content = json.dumps(self._body).encode() if self._body != "" else b""
        self.text = self.content.decode()

    def json(self):
        return self._body


class FakeTiny:
    """Simula accounts.tiny.com.br e api.tiny.com.br/public-api/v3."""

    def __init__(self):
        self.products: dict[int, dict] = {}
        self.orders: dict[int, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.next_id = 1000
        self.token_calls = 0
        self.fail_next_401 = False

    # requests.Session interface
    def post(self, url, data=None, headers=None, timeout=None, json=None):
        if url == tiny_client.TOKEN_URL:
            self.token_calls += 1
            grant = data["grant_type"]
            assert grant in ("authorization_code", "refresh_token")
            if grant == "authorization_code" and data["code"] != "codigo-ok":
                return FakeResponse(400, {"error": "invalid_grant"})
            return FakeResponse(200, {"access_token": f"acc-{self.token_calls}", "refresh_token": f"ref-{self.token_calls}", "expires_in": 14400, "refresh_expires_in": 86400})
        return self.request("POST", url, json=json, headers=headers)

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        path = url.replace(tiny_client.API_BASE, "")
        self.calls.append((method, path))
        if self.fail_next_401:
            self.fail_next_401 = False
            return FakeResponse(401, {"mensagem": "token expirado"})
        if not (headers or {}).get("Authorization", "").startswith("Bearer acc-"):
            return FakeResponse(401, {"mensagem": "sem token"})
        params = params or {}
        if method == "GET" and path == "/produtos":
            items = [p for p in self.products.values() if not params.get("codigo") or p["sku"] == params["codigo"]]
            return FakeResponse(200, {"itens": items, "paginacao": {"limit": 100, "offset": 0, "total": len(items)}})
        if method == "POST" and path == "/produtos":
            self.next_id += 1
            self.products[self.next_id] = {"id": self.next_id, "sku": json["sku"], "descricao": json["descricao"], "payload": json}
            return FakeResponse(200, {"id": self.next_id, "codigo": json["sku"], "descricao": json["descricao"]})
        if method == "PUT" and path.startswith("/produtos/") and path.endswith("/preco"):
            pid = int(path.split("/")[2])
            self.products[pid]["preco"] = json["preco"]
            return FakeResponse(204, "")
        if method == "PUT" and path.startswith("/produtos/"):
            pid = int(path.split("/")[2])
            self.products[pid]["payload"] = json
            return FakeResponse(204, "")
        if method == "POST" and path.startswith("/estoque/"):
            pid = int(path.split("/")[2])
            self.products[pid]["estoque"] = json["quantidade"]
            return FakeResponse(204, "")
        if method == "GET" and path == "/pedidos":
            items = [o for o in self.orders.values() if params.get("situacao") is None or o["situacao"] == params["situacao"]]
            summaries = [{k: o[k] for k in ("id", "numeroPedido", "situacao", "valor", "dataPrevista")} for o in items]
            return FakeResponse(200, {"itens": summaries, "paginacao": {"limit": 100, "offset": 0, "total": len(summaries)}})
        if method == "GET" and path.startswith("/pedidos/"):
            oid = int(path.split("/")[2])
            return FakeResponse(200, self.orders[oid]) if oid in self.orders else FakeResponse(404, {"mensagem": "não encontrado"})
        return FakeResponse(404, {"mensagem": f"rota desconhecida {method} {path}"})


def make_order(oid, numero, sku, qty=1, situacao=3, canal="Mercado Livre"):
    return {
        "id": oid, "numeroPedido": numero, "situacao": situacao, "valor": 177.9, "valorTotalPedido": 177.9,
        "dataPrevista": "2026-10-28", "cliente": {"nome": "Maria da Silva"},
        "ecommerce": {"canalVenda": canal, "numeroPedidoEcommerce": "2000001"},
        "itens": [{"produto": {"id": 1, "sku": sku, "descricao": "Luminária Solaris", "tipo": "P"}, "quantidade": qty, "valorUnitario": 177.9}],
    }


class TinyIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        cls.user = get_user_model().objects.create_superuser("carlos", "c@example.com", "senha-forte-123")
        cls.product = Product.objects.get(sku_base="TM-LUM-SOL")
        cls.variant = Variant.objects.get(sku="TM-LUM-SOL-G-BRC")
        cls.site = Channel.objects.get(name="Site")

    def setUp(self):
        self.fake = FakeTiny()
        self.conn = TinyConnection.get()
        self.conn.client_id, self.conn.client_secret = "cid", "secret"
        self.conn.price_channel = self.site
        self.conn.save()

    def connect(self):
        tiny_client.authorization_url(self.conn, "https://app.toomuch.art/integracoes/tiny/callback/")
        tiny_client.exchange_code(self.conn, "codigo-ok", self.conn.oauth_state, session=self.fake)
        return tiny_client.TinyClient(self.conn, session=self.fake, sleep=lambda s: None)

    def make_ready(self):
        Asset.objects.create(product=self.product, kind=Asset.Kind.PHOTO, is_main=True, file=SimpleUploadedFile("s.jpg", b"\xff\xd8\xff\xd9", content_type="image/jpeg"))
        for row in latest_prices(self.variant):
            approve_price(row, self.user, price=row.suggested_price)
        self.product.refresh_from_db()

    # --- OAuth -----------------------------------------------------------
    def test_authorization_url_and_code_exchange(self):
        url = tiny_client.authorization_url(self.conn, "https://app.toomuch.art/cb")
        self.assertTrue(url.startswith(tiny_client.AUTH_URL))
        self.assertIn("client_id=cid", url)
        self.assertIn(f"state={self.conn.oauth_state}", url)
        with self.assertRaises(tiny_client.TinyAuthError):
            tiny_client.exchange_code(self.conn, "codigo-ok", "estado-errado", session=self.fake)
        tiny_client.exchange_code(self.conn, "codigo-ok", self.conn.oauth_state, session=self.fake)
        self.conn.refresh_from_db()
        self.assertTrue(self.conn.connected)
        self.assertEqual(self.conn.access_token, "acc-1")
        self.assertTrue(self.conn.access_token_valid)

    def test_client_refreshes_expired_token_and_retries_on_401(self):
        client = self.connect()
        self.conn.access_expires_at = timezone.now() - timedelta(minutes=1)
        self.conn.save()
        client.list_products()
        self.assertEqual(self.fake.token_calls, 2)  # code + refresh
        self.fake.fail_next_401 = True
        client.list_products()
        self.assertEqual(self.fake.token_calls, 3)  # refresh após 401

    def test_expired_refresh_token_requires_reconnect(self):
        client = self.connect()
        self.conn.access_expires_at = timezone.now() - timedelta(hours=1)
        self.conn.refresh_expires_at = timezone.now() - timedelta(hours=1)
        self.conn.save()
        with self.assertRaises(tiny_client.TinyAuthError):
            client.list_products()
        self.conn.refresh_from_db()
        self.assertEqual(self.conn.status, TinyConnection.Status.ERROR)

    # --- Produtos --------------------------------------------------------
    def test_push_creates_then_updates_only_when_changed(self):
        client = self.connect()
        self.assertTrue(services.push_variant(self.variant, client).startswith("skipped"))  # sem foto/preço aprovado
        self.make_ready()
        self.assertEqual(services.push_variant(self.variant, client, base_url="https://app.toomuch.art/"), "created")
        link = TinyProductLink.objects.get(variant=self.variant)
        tiny_product = self.fake.products[link.tiny_id]
        payload = tiny_product["payload"]
        self.assertEqual(payload["sku"], "TM-LUM-SOL-G-BRC")
        self.assertEqual(payload["descricao"], "Luminária Solaris G Branco")
        self.assertEqual(payload["precos"]["preco"], 118.9)
        self.assertEqual(payload["dimensoes"]["altura"], 27.0)
        self.assertEqual(payload["dimensoes"]["pesoLiquido"], 0.45)
        self.assertTrue(payload["anexos"][0]["url"].startswith("https://app.toomuch.art/media/"))
        self.assertEqual(link.last_price, D("118.90"))

        self.assertEqual(services.push_variant(self.variant, client, base_url="https://app.toomuch.art/"), "unchanged")
        row = next(r for r in latest_prices(self.variant) if r.channel_id == self.site.pk)
        approve_price(row, self.user, price=D("129.90"))
        self.assertEqual(services.push_variant(self.variant, client, base_url="https://app.toomuch.art/"), "updated")
        self.assertEqual(self.fake.products[link.tiny_id]["preco"], 129.9)

    def test_push_links_existing_product_by_sku(self):
        client = self.connect()
        self.make_ready()
        self.fake.products[77] = {"id": 77, "sku": "TM-LUM-SOL-G-BRC", "descricao": "já existia"}
        self.assertEqual(services.push_variant(self.variant, client), "updated")
        self.assertEqual(TinyProductLink.objects.get(variant=self.variant).tiny_id, 77)

    def test_push_all_reports(self):
        client = self.connect()
        self.make_ready()
        report = services.push_all(client, base_url="https://app.toomuch.art/")
        self.assertEqual((report.created, report.updated, report.unchanged), (1, 0, 0))
        self.assertEqual(TinySyncLog.objects.filter(kind="products").count(), 1)

    # --- Pedidos ---------------------------------------------------------
    def test_orders_become_production_orders_once(self):
        client = self.connect()
        self.fake.orders[501] = make_order(501, 1501, "TM-LUM-SOL-G-BRC", qty=2)
        self.fake.orders[502] = make_order(502, 1502, "SKU-DESCONHECIDO")
        self.fake.orders[503] = make_order(503, 1503, "TM-LUM-SOL-G-BRC", situacao=0)  # aberta: não entra
        report = services.sync_orders(client, user=self.user)
        self.assertEqual(report.seen, 2)
        self.assertEqual(report.created_orders, 1)
        self.assertEqual(report.skipped, 1)
        po = ProductionOrder.objects.get(origin=ProductionOrder.Origin.TINY)
        self.assertEqual(po.quantity, 2)
        self.assertEqual(po.external_id, "1501")
        self.assertEqual(str(po.due_date), "2026-10-28")
        self.assertEqual(po.jobs.count(), 4)  # 2 peças × 2 unidades
        self.assertIn("Mercado Livre", po.notes)
        sync = TinyOrderSync.objects.get(tiny_order_id=501)
        self.assertEqual(sync.result, TinyOrderSync.Result.CREATED)
        self.assertEqual(TinyOrderSync.objects.get(tiny_order_id=502).result, TinyOrderSync.Result.SKIPPED)

        again = services.sync_orders(client, user=self.user)
        self.assertEqual(again.created_orders, 0)
        self.assertEqual(ProductionOrder.objects.filter(origin="tiny").count(), 1)

    def test_management_command(self):
        self.connect()
        self.fake.orders[600] = make_order(600, 1600, "TM-LUM-SOL-G-BRC")
        real = tiny_client.TinyClient
        with mock.patch("apps.integrations.tiny.management.commands.tiny_sync.TinyClient", lambda conn=None: real(conn, session=self.fake, sleep=lambda s: None)):
            call_command("tiny_sync", so_pedidos=True)
        self.assertEqual(ProductionOrder.objects.filter(origin="tiny").count(), 1)

    # --- Tela ------------------------------------------------------------
    def test_status_page_and_config_form(self):
        self.client.login(username="carlos", password="senha-forte-123")
        resp = self.client.get(reverse("tiny:status"))
        self.assertContains(resp, "Tiny ERP")
        self.assertContains(resp, "/integracoes/tiny/callback/")
        resp = self.client.post(reverse("tiny:status"), {
            "client_id": "novo", "client_secret": "s", "price_channel": self.site.pk, "product_push_enabled": "on",
            "order_sync_enabled": "on", "order_situacoes": "3,1", "order_lookback_days": 10, "default_priority": 1,
        })
        self.assertRedirects(resp, reverse("tiny:status"))
        self.conn.refresh_from_db()
        self.assertEqual(self.conn.situacoes, [3, 1])
        resp = self.client.get(reverse("tiny:connect"))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].startswith(tiny_client.AUTH_URL))
        resp = self.client.get(reverse("tiny:callback"), {"error": "access_denied"})
        self.assertRedirects(resp, reverse("tiny:status"))
