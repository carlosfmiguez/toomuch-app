"""
Serviços de sincronização com o Tiny:

- push_variant / push_all: variantes prontas para catálogo viram produtos no Tiny
  (cria ou atualiza pelo SKU), com preço do canal escolhido, medidas, peso, fotos e
  estoque pronto. Idempotente: só reenvia se o payload mudou.
- sync_orders: pedidos nas situações configuradas (padrão: 3 = Aprovada) viram
  ordens de produção, uma por item cujo SKU exista aqui. Cada pedido é processado
  uma única vez (TinyOrderSync).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Asset, Product, Variant
from apps.costing.services import latest_prices
from apps.production.models import ProductionOrder

from .client import TinyClient, TinyError
from .models import TinyConnection, TinyOrderSync, TinyProductLink, TinySyncLog


@dataclass
class PushReport:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class OrderReport:
    seen: int = 0
    created_orders: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Produtos
# ---------------------------------------------------------------------------


def _price_for(variant: Variant, conn: TinyConnection) -> Decimal | None:
    if conn.price_channel_id is None:
        return None
    for row in latest_prices(variant):
        if row.channel_id == conn.price_channel_id:
            return row.current_price
    return None


def _photo_urls(product: Product, base_url: str) -> list[str]:
    urls = []
    for a in product.assets.filter(kind__in=[Asset.Kind.PHOTO, Asset.Kind.RENDER]).order_by("-is_main", "order")[:6]:
        if a.file:
            url = a.file.url
            urls.append(base_url.rstrip("/") + url if url.startswith("/") else url)
    return urls


def build_product_payload(variant: Variant, conn: TinyConnection, base_url: str = "") -> dict:
    product = variant.product
    name = product.name
    if variant.size and variant.size.lower() not in ("único", "unico"):
        name += f" {variant.size}"
    if variant.primary_color:
        name += f" {variant.primary_color.name}"
    price = _price_for(variant, conn)
    payload: dict = {
        "sku": variant.sku,
        "descricao": name[:120],
        "tipo": "S",
        "unidade": "UN",
        "descricaoComplementar": product.long_description or product.short_description or "",
        "origem": 0,
        "garantia": "90 dias contra defeitos de fabricação",
        "estoque": {"controlar": True, "sobEncomenda": True, "diasPreparacao": int(product.lead_time_days or 5), "minimo": 0},
    }
    if price is not None:
        payload["precos"] = {"preco": float(price), "precoCusto": float(variant.cost_cached or 0)}
    dims = {}
    if product.width_mm:
        dims["largura"] = round(product.width_mm / 10, 2)
    if product.height_mm:
        dims["altura"] = round(product.height_mm / 10, 2)
    if product.depth_mm:
        dims["comprimento"] = round(product.depth_mm / 10, 2)
    if product.weight_g:
        dims["pesoLiquido"] = round(product.weight_g / 1000, 3)
        dims["pesoBruto"] = round((product.weight_g + 120) / 1000, 3)  # caixa e papel
    if dims:
        payload["dimensoes"] = dims
    photos = _photo_urls(product, base_url) if base_url else []
    if photos:
        payload["anexos"] = [{"url": u, "externo": True} for u in photos]
    if product.tags:
        payload["seo"] = {"keywords": [t.strip() for t in product.tags.split(",") if t.strip()][:10]}
    return payload


def _payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@transaction.atomic
def push_variant(variant: Variant, client: TinyClient, base_url: str = "", force: bool = False) -> str:
    """Retorna 'created', 'updated', 'unchanged' ou 'skipped:<motivo>'."""
    conn = client.conn
    if not variant.product.ready_for_catalog:
        return "skipped:produto não está pronto para catálogo"
    payload = build_product_payload(variant, conn, base_url)
    if "precos" not in payload:
        return "skipped:sem preço no canal configurado"
    digest = _payload_hash(payload)
    link = TinyProductLink.objects.filter(variant=variant).first()
    if link and link.last_payload_hash == digest and not force:
        return "unchanged"

    if link is None:
        existing = client.find_product_by_sku(variant.sku)
        if existing:
            link = TinyProductLink.objects.create(variant=variant, tiny_id=int(existing["id"]), tiny_sku=variant.sku)
    try:
        if link is None:
            created = client.create_product(payload)
            link = TinyProductLink.objects.create(variant=variant, tiny_id=int(created["id"]), tiny_sku=variant.sku)
            result = "created"
        else:
            client.update_product(link.tiny_id, payload)
            client.update_price(link.tiny_id, payload["precos"]["preco"], preco_custo=payload["precos"].get("precoCusto"))
            result = "updated"
        if variant.stock_ready:
            client.update_stock(link.tiny_id, variant.stock_ready, kind="B", observacoes="Sincronização Sistema Toomuch")
    except TinyError as exc:
        if link:
            link.last_error = str(exc)[:300]
            link.save(update_fields=["last_error"])
        raise
    link.last_pushed_at = timezone.now()
    link.last_price = Decimal(str(payload["precos"]["preco"]))
    link.last_payload_hash = digest
    link.last_error = ""
    link.save()
    return result


def push_all(client: TinyClient | None = None, base_url: str = "", force: bool = False) -> PushReport:
    client = client or TinyClient()
    report = PushReport()
    variants = Variant.objects.filter(product__status__in=[Product.Status.ACTIVE, Product.Status.SEASONAL]).select_related("product", "primary_color")
    for variant in variants:
        try:
            result = push_variant(variant, client, base_url, force)
        except TinyError as exc:
            report.errors.append(f"{variant.sku}: {exc}")
            continue
        if result == "created":
            report.created += 1
        elif result == "updated":
            report.updated += 1
        elif result == "unchanged":
            report.unchanged += 1
        else:
            report.skipped.append(f"{variant.sku}: {result.split(':', 1)[1]}")
    TinySyncLog.objects.create(
        kind=TinySyncLog.Kind.PRODUCTS, ok=not report.errors,
        summary=f"{report.created} criados, {report.updated} atualizados, {report.unchanged} sem mudança, {len(report.skipped)} pulados, {len(report.errors)} erros",
        details={"skipped": report.skipped[:50], "errors": report.errors[:50]},
    )
    return report


# ---------------------------------------------------------------------------
# Pedidos → ordens de produção
# ---------------------------------------------------------------------------


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


@transaction.atomic
def process_order(order: dict, conn: TinyConnection, user=None) -> TinyOrderSync:
    """Cria ordens de produção para os itens do pedido cujo SKU exista aqui. Uma vez por pedido."""
    tiny_id = int(order["id"])
    existing = TinyOrderSync.objects.filter(tiny_order_id=tiny_id).first()
    if existing:
        return existing
    numero = str(order.get("numeroPedido") or "")
    eco = order.get("ecommerce") or {}
    canal = eco.get("canalVenda") or eco.get("nome") or ""
    cliente = (order.get("cliente") or {}).get("nome") or ""
    due = _parse_date(order.get("dataPrevista"))
    sync = TinyOrderSync(
        tiny_order_id=tiny_id, numero_pedido=numero, situacao=order.get("situacao"), canal=canal[:80], cliente=cliente[:160],
        valor=Decimal(str(order.get("valorTotalPedido") or order.get("valor") or 0)), data_prevista=due, raw=order,
    )
    created = []
    unknown = []
    for item in order.get("itens", []):
        prod = item.get("produto") or {}
        sku = (prod.get("sku") or prod.get("codigo") or "").strip()
        qty = int(Decimal(str(item.get("quantidade") or 1)))
        variant = Variant.objects.filter(sku__iexact=sku).select_related("product").first() if sku else None
        if variant is None:
            unknown.append(sku or prod.get("descricao", "?"))
            continue
        po = ProductionOrder.objects.create(
            origin=ProductionOrder.Origin.TINY, external_id=f"{numero or tiny_id}", variant=variant, quantity=max(qty, 1),
            priority=conn.default_priority, due_date=due, created_by=user,
            notes=f"Pedido Tiny {numero or tiny_id} · {canal} · {cliente}".strip(" ·"),
        )
        po.explode()
        created.append(po)
    if created:
        sync.result = TinyOrderSync.Result.CREATED
        sync.detail = f"{len(created)} ordem(ns)" + (f"; SKUs desconhecidos: {', '.join(unknown)}" if unknown else "")
    else:
        sync.result = TinyOrderSync.Result.SKIPPED
        sync.detail = "Nenhum item com SKU cadastrado aqui" + (f": {', '.join(unknown)}" if unknown else "")
    sync.save()
    sync.orders.set(created)
    return sync


def sync_orders(client: TinyClient | None = None, user=None, since: date | None = None) -> OrderReport:
    client = client or TinyClient()
    conn = client.conn
    report = OrderReport()
    since = since or (timezone.localdate() - timezone.timedelta(days=conn.order_lookback_days))
    known = set(TinyOrderSync.objects.values_list("tiny_order_id", flat=True))
    for situacao in conn.situacoes:
        try:
            for summary in client.iter_orders(situacao=situacao, data_inicial=since.isoformat()):
                report.seen += 1
                tiny_id = int(summary["id"])
                if tiny_id in known:
                    continue
                try:
                    order = client.get_order(tiny_id)
                    sync = process_order(order, conn, user)
                    known.add(tiny_id)
                    if sync.result == TinyOrderSync.Result.CREATED:
                        report.created_orders += sync.orders.count()
                    else:
                        report.skipped += 1
                except (TinyError, KeyError, ValueError) as exc:
                    report.errors.append(f"pedido {tiny_id}: {exc}")
        except TinyError as exc:
            report.errors.append(f"situação {situacao}: {exc}")
    conn.last_order_sync_at = timezone.now()
    conn.save(update_fields=["last_order_sync_at"])
    TinySyncLog.objects.create(
        kind=TinySyncLog.Kind.ORDERS, ok=not report.errors,
        summary=f"{report.seen} pedidos vistos, {report.created_orders} ordens criadas, {report.skipped} sem itens conhecidos, {len(report.errors)} erros",
        details={"errors": report.errors[:50]},
    )
    return report
