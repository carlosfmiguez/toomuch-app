"""
Exportação de anúncios para planilhas de marketplace (Mercado Livre e Shopee).

Gera uma linha por variante com título dentro do limite, descrição estruturada,
preço vigente do canal, medidas, peso e fotos. Os cabeçalhos seguem os campos
usuais das planilhas de carga em massa; ajuste o mapeamento se a planilha
oficial do marketplace mudar.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

from django.utils import timezone

from apps.catalog.models import Asset, Product, Variant
from apps.costing.models import Channel
from apps.costing.services import latest_prices

TITLE_LIMITS = {"ml": 60, "shopee": 120}

COLUMNS = {
    "ml": [
        "SKU", "Título", "Descrição", "Preço", "Quantidade", "Condição", "Categoria", "Marca", "Modelo", "Cor",
        "Altura (cm)", "Largura (cm)", "Comprimento (cm)", "Peso (g)", "Fotos", "Garantia", "Prazo de produção (dias úteis)",
    ],
    "shopee": [
        "SKU", "Nome do produto", "Descrição", "Preço", "Estoque", "Categoria", "Marca", "Variação (cor)", "Tamanho",
        "Peso (kg)", "Comprimento (cm)", "Largura (cm)", "Altura (cm)", "Imagens", "Prazo de envio (dias)",
    ],
}


def build_title(product: Product, variant: Variant, limit: int) -> str:
    bits = [product.category.name.split(" ")[0] if product.category_id else "", "3D", product.name]
    if variant.size and variant.size.lower() not in ("único", "unico"):
        bits.append(variant.size)
    if variant.primary_color:
        bits.append(variant.primary_color.name)
    title = " ".join(b for b in bits if b).replace("  ", " ")
    return title[:limit].rstrip()


def build_description(product: Product, variant: Variant, price_list_note: str = "") -> str:
    colors = ", ".join(sorted({v.primary_color.name for v in product.variants.all() if v.primary_color}))
    lines = [
        f"{product.name.upper()} — DECORAÇÃO 3D TOOMUCH ART",
        "",
        product.short_description or product.long_description,
        "",
        "DIFERENCIAIS",
        "• Impressão 3D de alta definição, camada por camada",
        "• Design autoral, produzido sob demanda em Florianópolis",
        "• Leve, resistente e com acabamento sofisticado",
    ]
    if product.height_mm or product.width_mm or product.weight_g:
        lines += ["", "DIMENSÕES"]
        if product.height_mm:
            lines.append(f"• Altura: {product.height_mm / 10:g} cm")
        if product.width_mm:
            lines.append(f"• Largura/diâmetro: {product.width_mm / 10:g} cm")
        if product.depth_mm and product.depth_mm != product.width_mm:
            lines.append(f"• Profundidade: {product.depth_mm / 10:g} cm")
        if product.weight_g:
            lines.append(f"• Peso: {product.weight_g} g")
    if colors:
        lines += ["", "CORES DISPONÍVEIS", colors + " (consulte outras)"]
    if product.long_description and product.short_description:
        lines += ["", product.long_description]
    lines += ["", "PRAZO", f"Produção sob demanda: {product.lead_time_days} dias úteis + frete"]
    if product.license_attribution_required and product.license_author:
        lines += ["", f"Modelo: {product.license_author}"]
    lines += ["", "⚠ Não expor ao sol direto por longos períodos"]
    return "\n".join(lines)


def photo_urls(product: Product, base_url: str = "") -> list[str]:
    urls = []
    for a in product.assets.filter(kind__in=[Asset.Kind.PHOTO, Asset.Kind.RENDER]).order_by("-is_main", "order"):
        if a.file:
            url = a.file.url
            if url.startswith("/") and base_url:
                url = base_url.rstrip("/") + url
            urls.append(url)
    return urls


def _price(variant: Variant, channel: Channel | None) -> Decimal | None:
    if channel is None:
        return None
    for row in latest_prices(variant):
        if row.channel_id == channel.pk:
            return row.current_price
    return None


def build_export(fmt: str, channel: Channel | None, only_ready: bool = True, base_url: str = "") -> tuple[list[dict], list[str]]:
    if fmt not in COLUMNS:
        raise ValueError("Formato desconhecido")
    columns = COLUMNS[fmt]
    rows: list[dict] = []
    products = Product.objects.filter(status__in=[Product.Status.ACTIVE, Product.Status.SEASONAL]).select_related("category")
    for product in products:
        if only_ready and not product.ready_for_catalog:
            continue
        if not product.license_is_valid:
            continue
        photos = " | ".join(photo_urls(product, base_url))
        for variant in product.variants.select_related("primary_color"):
            price = _price(variant, channel)
            if price is None:
                continue
            title = build_title(product, variant, TITLE_LIMITS[fmt])
            description = build_description(product, variant)
            if fmt == "ml":
                rows.append({
                    "SKU": variant.sku, "Título": title, "Descrição": description, "Preço": str(price),
                    "Quantidade": variant.stock_ready or 1, "Condição": "Novo", "Categoria": product.category.name,
                    "Marca": "Toomuch Art", "Modelo": product.name, "Cor": variant.primary_color.name if variant.primary_color else "",
                    "Altura (cm)": _cm(product.height_mm), "Largura (cm)": _cm(product.width_mm), "Comprimento (cm)": _cm(product.depth_mm),
                    "Peso (g)": product.weight_g or "", "Fotos": photos, "Garantia": "90 dias contra defeitos de fabricação",
                    "Prazo de produção (dias úteis)": product.lead_time_days,
                })
            else:
                rows.append({
                    "SKU": variant.sku, "Nome do produto": title, "Descrição": description, "Preço": str(price),
                    "Estoque": variant.stock_ready or 1, "Categoria": product.category.name, "Marca": "Toomuch Art",
                    "Variação (cor)": variant.primary_color.name if variant.primary_color else "", "Tamanho": variant.size,
                    "Peso (kg)": f"{(product.weight_g or 0) / 1000:.3f}", "Comprimento (cm)": _cm(product.depth_mm),
                    "Largura (cm)": _cm(product.width_mm), "Altura (cm)": _cm(product.height_mm), "Imagens": photos,
                    "Prazo de envio (dias)": product.lead_time_days,
                })
    return rows, columns


def _cm(mm) -> str:
    return f"{mm / 10:g}" if mm else ""


def write_csv(rows: list[dict], columns: list[str]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, delimiter=";", quoting=csv.QUOTE_ALL)
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns})
    return ("﻿" + buf.getvalue()).encode("utf-8")


def write_xlsx(rows: list[dict], columns: list[str]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Anúncios"
    ws.append(columns)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append([row.get(c, "") for c in columns])
    for col in ws.columns:
        width = min(60, max(12, max(len(str(c.value or "")) for c in col) + 2))
        ws.column_dimensions[col[0].column_letter].width = width
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def export_filename(fmt: str, ext: str) -> str:
    return f"anuncios-{fmt}-{timezone.localdate():%Y-%m-%d}.{ext}"
