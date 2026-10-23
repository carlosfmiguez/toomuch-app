"""
Ferramentas do conector MCP: o que o Claude pode fazer no sistema.

Cada ferramenta é uma função (usuário, **argumentos) → dict, registrada com nome,
descrição e JSON Schema. As regras de negócio são as mesmas da ficha do produto
(apps.catalog.workflow): custo e preço recalculados sozinhos, SKU automático etc.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

from django.core.files import File
from django.db import transaction
from django.db.models import Q
from django.urls import reverse

from apps.catalog import importer, workflow
from apps.catalog.models import Asset, BOMItem, Category, Collection, Color, Component, Part, Product, Variant
from apps.costing.models import Channel, GlobalParams, Printer
from apps.costing.services import approve_price, latest_prices
from apps.inventory.models import FilamentSKU, Purchase

TOOLS: dict[str, dict] = {}
MAX_DOWNLOAD_MB = 400


class ToolError(Exception):
    """Erro de uso (argumento inválido, item não encontrado): vira resposta com isError."""


def tool(name: str, description: str, properties: dict | None = None, required: list[str] | None = None, readonly: bool = False):
    def deco(fn):
        TOOLS[name] = {
            "fn": fn,
            "spec": {
                "name": name,
                "description": description,
                "inputSchema": {"type": "object", "properties": properties or {}, "required": required or [],
                                "additionalProperties": False},
                "annotations": {"readOnlyHint": readonly, "destructiveHint": False},
            },
        }
        return fn
    return deco


S = {"type": "string"}
N = {"type": "number"}
I = {"type": "integer"}
B = {"type": "boolean"}


# --- helpers ------------------------------------------------------------------

def _dec(value, field: str) -> Decimal:
    try:
        s = str(value).strip().replace("R$", "").replace(" ", "")
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        return Decimal(s)
    except (InvalidOperation, ValueError):
        raise ToolError(f"{field}: número inválido ({value!r})")


def _product(sku_or_id) -> Product:
    q = str(sku_or_id).strip()
    p = Product.objects.filter(sku_base__iexact=q).first() or (Product.objects.filter(pk=q).first() if q.isdigit() else None)
    if p is None:
        p = Product.objects.filter(name__iexact=q).first()
    if p is None:
        raise ToolError(f"Produto não encontrado: {q}. Use listar_produtos para ver os SKUs.")
    return p


def _variant(sku) -> Variant:
    v = Variant.objects.filter(sku__iexact=str(sku).strip()).select_related("product").first()
    if v is None:
        raise ToolError(f"Variante não encontrada: {sku}.")
    return v


def _filament(ref) -> FilamentSKU | None:
    if ref in (None, ""):
        return None
    q = str(ref).strip()
    if q.isdigit():
        f = FilamentSKU.objects.filter(pk=q).first()
        if f:
            return f
    words = q.lower().split()
    qs = FilamentSKU.objects.filter(active=True)
    for w in words:
        qs = qs.filter(Q(brand__icontains=w) | Q(material__icontains=w) | Q(color_name__icontains=w) | Q(manufacturer__icontains=w))
    found = list(qs[:5])
    if len(found) == 1:
        return found[0]
    if not found:
        raise ToolError(f"Filamento não encontrado: {q}. Use listar_filamentos (ou criar_filamento).")
    raise ToolError(f"Filamento ambíguo: {q}. Opções: " + "; ".join(f"{f.pk} = {f}" for f in found))


def _color(ref) -> Color | None:
    if ref in (None, ""):
        return None
    q = str(ref).strip()
    c = Color.objects.filter(Q(code__iexact=q) | Q(name__iexact=q)).first()
    if c is None:
        raise ToolError(f"Cor não encontrada: {q}. Use listar_cadastros para ver as cores.")
    return c


def _category(ref) -> Category:
    q = str(ref).strip()
    c = Category.objects.filter(Q(code__iexact=q) | Q(name__iexact=q)).first()
    if c is None:
        raise ToolError(f"Categoria não encontrada: {q}. Opções: " + ", ".join(f"{c.code} ({c.name})" for c in Category.objects.all()))
    return c


def _collection(ref) -> Collection:
    q = str(ref).strip()
    c = Collection.objects.filter(Q(name__iexact=q) | Q(slug__iexact=q)).first() or (Collection.objects.filter(pk=q).first() if q.isdigit() else None)
    if c is None:
        raise ToolError(f"Coleção não encontrada: {q}. Opções: " + (", ".join(c.name for c in Collection.objects.all()) or "nenhuma ainda"))
    return c


def _collection_info(c: Collection, base: str) -> dict:
    return {"id": c.pk, "nome": c.name, "slug": c.slug, "descricao": c.description, "mostrar_no_portal": c.show_on_site,
            "ordem": c.order, "produtos": [p.sku_base for p in c.products.order_by("name")],
            "portal": base + reverse("public:collection", args=[c.slug])}


def _url(request_base: str, name: str, *args) -> str:
    return request_base + reverse(name, args=args)


def _price_rows(v: Variant) -> list[dict]:
    return [{
        "canal": r.channel.name, "sugerido": str(r.suggested_price), "vigente": str(r.current_price),
        "margem_pct": str(r.margin_pct), "aprovado": r.approved, "abaixo_da_margem_minima": r.below_minimum,
    } for r in latest_prices(v)]


def _variant_info(v: Variant) -> dict:
    bd = v.cost_breakdown or {}
    return {
        "sku": v.sku, "tamanho": v.size, "padrao": v.is_default,
        "cor_principal": v.primary_color.name if v.primary_color else None,
        "cor_secundaria": v.secondary_color.name if v.secondary_color else None,
        "filamento_principal": str(v.main_filament) if v.main_filament else None,
        "filamento_secundario": str(v.secondary_filament) if v.secondary_filament else None,
        "minutos": {"pos_processamento": v.minutes_post, "montagem": v.minutes_assembly, "embalagem": v.minutes_packaging},
        "composicao": [f"{b.quantity} × {b.part or b.component}" for b in v.bom_items.select_related("part", "component")],
        "custo": str(v.cost_cached) if v.cost_cached is not None else None,
        "custo_detalhe": {k: bd.get(k) for k in ("filamento", "hora_maquina", "mao_de_obra", "componentes", "embalagem", "licenca", "falha")},
        "problemas_no_custo": bd.get("problemas", []),
        "precos": _price_rows(v),
    }


def _product_info(p: Product, base: str, full: bool = True) -> dict:
    steps = workflow.steps_for(p)
    info = {
        "sku_base": p.sku_base, "nome": p.name, "categoria": p.category.name,
        "colecoes": [c.name for c in p.collections.all()], "status": p.get_status_display(),
        "origem": p.get_origin_display(), "licenca_valida": p.license_is_valid,
        "pronto_para_catalogo": p.ready_for_catalog,
        "etapas": {s.title: ("feito" if s.done else "pendente: " + "; ".join(s.todo)) for s in steps},
        "ficha": _url(base, "catalog:product_detail", p.pk),
    }
    if full:
        info.update({
            "descricao_curta": p.short_description, "descricao_longa": p.long_description, "tags": p.tags,
            "medidas_mm": [p.height_mm, p.width_mm, p.depth_mm], "peso_g": p.weight_g, "prazo_dias": p.lead_time_days,
            "licenca": {"autor": p.license_author, "link": p.license_url, "termos": p.license_terms,
                        "custo_por_unidade": str(p.license_cost_per_unit), "exige_atribuicao": p.license_attribution_required},
            "pecas": [{
                "id": part.pk, "nome": part.name, "qtd": part.default_qty, "slot_cor": part.get_color_slot_display(),
                "so_nos_tamanhos": part.only_sizes or None,
                "estimativa": {"gramas": str(part.est_grams), "minutos": part.est_minutes} if part.has_estimate else None,
                "compartilhada_de": part.product.sku_base if part.product_id != p.pk else None,
                "placa_do_3mf": part.plate_index,
                "arquivos": [{"id": a.pk, "nome": a.name, "tipo": a.get_kind_display(), "producao": a.is_production,
                              "gramas": str(a.grams_total) if a.grams_total else None, "minutos": a.print_time_min,
                              "placas": [{"placa": pl.get("index"), "nome": Asset.plate_label(pl), "gramas": str(Asset.plate_grams(pl)),
                                          "minutos": round(int(pl.get("time_s") or 0) / 60), "objetos": pl.get("objects", [])}
                                         for pl in a.plates] if len(a.plates) > 1 else None,
                              "placas_sao": {"sizes": "tamanhos", "parts": "partes"}.get(a.plates_mode) if len(a.plates) > 1 else None,
                              "decidir_placas": a.plates_pending,
                              "erro": a.parse_error or None} for a in part.assets.all()],
            } for part in p.all_parts()],
            "fotos": [{"id": a.pk, "nome": a.name, "principal": a.is_main} for a in p.assets.filter(kind__in=["photo", "render"])],
            "variantes": [_variant_info(v) for v in p.variants.select_related("primary_color", "secondary_color", "main_filament", "secondary_filament")],
        })
    return info


# --- leitura -------------------------------------------------------------------

@tool("resumo", "Visão geral do sistema: quantos produtos por status, o que está pendente (3MF, custo, preço a aprovar, fotos), filamentos abaixo do mínimo e parâmetros de custo vigentes.", readonly=True)
def resumo(user, base):
    from apps.core.dashboard import pending_items

    params = GlobalParams.current()
    pend = pending_items()
    by_kind: dict[str, int] = {}
    for it in pend:
        by_kind[it["kind"]] = by_kind.get(it["kind"], 0) + 1
    return {
        "produtos": {label: Product.objects.filter(status=v).count() for v, label in Product.Status.choices},
        "prontos_para_catalogo": sum(1 for p in Product.objects.exclude(status="discontinued") if p.ready_for_catalog),
        "pendencias": by_kind,
        "precos_a_aprovar": len(workflow.pending_approvals()),
        "filamentos_abaixo_do_minimo": [str(f) for f in FilamentSKU.objects.filter(active=True) if f.below_minimum],
        "parametros": {"energia_kwh": str(params.energy_tariff_kwh), "mao_de_obra_h": str(params.labor_cost_per_hour),
                       "desperdicio_pct": str(params.waste_pct), "falha_pct": str(params.failure_pct),
                       "simples_pct": str(params.simples_pct), "embalagem_padrao": str(params.packaging_default_cost)},
    }


@tool("listar_produtos", "Lista produtos com status, categoria, coleções, etapas concluídas e o próximo passo. Filtros opcionais.", {
    "busca": {**S, "description": "parte do nome ou SKU"},
    "categoria": {**S, "description": "código (NAT, BAN...) ou nome"},
    "colecao": {**S, "description": "nome ou slug da coleção"},
    "status": {"type": "string", "enum": ["draft", "active", "seasonal", "discontinued"]},
    "limite": {**I, "description": "padrão 100"},
}, readonly=True)
def listar_produtos(user, base, busca="", categoria="", status="", limite=100, colecao=""):
    qs = Product.objects.select_related("category").prefetch_related("collections").order_by("name")
    if colecao:
        qs = qs.filter(collections=_collection(colecao))
    if busca:
        qs = qs.filter(Q(name__icontains=busca) | Q(sku_base__icontains=busca))
    if categoria:
        qs = qs.filter(category=_category(categoria))
    if status:
        qs = qs.filter(status=status)
    out = []
    for p in qs[: int(limite or 100)]:
        steps = workflow.steps_for(p)
        nxt = workflow.next_step(steps)
        out.append({"sku_base": p.sku_base, "nome": p.name, "categoria": p.category.code,
                    "colecoes": [c.name for c in p.collections.all()], "status": p.status,
                    "etapas_feitas": f"{sum(s.done for s in steps)}/{len(steps)}",
                    "proximo_passo": f"{nxt.title}: {'; '.join(nxt.todo[:2])}" if nxt else "pronto",
                    "variantes": p.variants.count()})
    return {"total": qs.count(), "produtos": out}


@tool("ver_produto", "Tudo de um produto: dados, licença, peças e arquivos (gramas/tempo), variantes com custo detalhado e preços por canal, fotos e etapas pendentes.", {
    "produto": {**S, "description": "SKU-base (ex.: TM-NAT-SIN), id ou nome exato"},
}, ["produto"], readonly=True)
def ver_produto(user, base, produto):
    return _product_info(_product(produto), base)


@tool("listar_cadastros", "Categorias, coleções (linhas de design), cores, canais de venda (com taxas e margens), impressoras e componentes/embalagens cadastrados.", readonly=True)
def listar_cadastros(user, base):
    chans = []
    for c in Channel.objects.all():
        fee = c.current_fee()
        chans.append({"nome": c.name, "status": c.status, "taxa_pct": str(fee.fee_pct) if fee else None,
                      "margem_alvo_pct": str(fee.target_margin_pct) if fee else None,
                      "margem_minima_pct": str(fee.min_margin_pct) if fee else None})
    return {
        "categorias": [{"codigo": c.code, "nome": c.name} for c in Category.objects.all()],
        "colecoes": [{"nome": c.name, "slug": c.slug, "produtos": c.products.count(), "mostrar_no_portal": c.show_on_site}
                     for c in Collection.objects.all()],
        "cores": [{"codigo": c.code, "nome": c.name, "hex": c.hex} for c in Color.objects.all()],
        "canais": chans,
        "impressoras": [{"nome": p.name, "modelo": p.model, "status": p.status, "custo_hora": str(p.hourly_cost())} for p in Printer.objects.all()],
        "componentes": [{"id": c.pk, "nome": c.name, "custo": str(c.unit_cost), "embalagem": c.is_packaging} for c in Component.objects.filter(active=True)],
    }


@tool("listar_filamentos", "Filamentos com id, custo médio por kg (vem das compras), saldo em gramas e se está abaixo do mínimo.", {
    "busca": {**S, "description": "marca, material ou cor"},
}, readonly=True)
def listar_filamentos(user, base, busca=""):
    qs = FilamentSKU.objects.filter(active=True)
    for w in (busca or "").split():
        qs = qs.filter(Q(brand__icontains=w) | Q(material__icontains=w) | Q(color_name__icontains=w))
    return {"filamentos": [{"id": f.pk, "nome": str(f), "custo_kg": str(f.avg_cost_per_kg), "saldo_g": str(f.stock_g),
                            "abaixo_do_minimo": f.below_minimum, "cor_sku": f.color.code if f.color else None} for f in qs]}


@tool("precos_a_aprovar", "Variantes com preço sugerido ainda não aprovado em algum canal.", readonly=True)
def precos_a_aprovar(user, base):
    return {"itens": [{"sku": it["variant"].sku, "produto": it["variant"].product.name, "custo": str(it["variant"].cost_cached),
                       "precos": _price_rows(it["variant"])} for it in workflow.pending_approvals()]}


# --- escrita: produto ------------------------------------------------------------

PRODUCT_FIELDS = {
    "nome": "name", "descricao_curta": "short_description", "descricao_longa": "long_description", "tags": "tags",
    "altura_mm": "height_mm", "largura_mm": "width_mm", "profundidade_mm": "depth_mm", "peso_g": "weight_g",
    "prazo_dias": "lead_time_days", "licenca_autor": "license_author", "licenca_link": "license_url",
    "licenca_termos": "license_terms", "licenca_exige_atribuicao": "license_attribution_required",
}
ORIGINS = {"autoral": "own", "own": "own", "licenciado": "licensed", "licensed": "licensed",
           "sem_licenca": "unlicensed", "unlicensed": "unlicensed"}
PRODUCT_PROPS = {
    "nome": S, "descricao_curta": S, "descricao_longa": S, "tags": S, "altura_mm": I, "largura_mm": I,
    "profundidade_mm": I, "peso_g": I, "prazo_dias": I,
    "origem": {"type": "string", "enum": ["autoral", "licenciado", "sem_licenca"]},
    "licenca_autor": S, "licenca_link": S, "licenca_termos": S, "licenca_custo_por_unidade": N,
    "licenca_exige_atribuicao": B, "categoria": S,
    "colecoes": {"type": "array", "items": S,
                 "description": "Nomes das coleções (linha de design). Substitui as atuais; [] remove todas. Coleção que não existe é criada."},
}


def _apply_product_fields(p: Product, data: dict) -> list[str]:
    changed = []
    for key, field in PRODUCT_FIELDS.items():
        if key in data and data[key] is not None:
            setattr(p, field, data[key])
            changed.append(key)
    if data.get("origem"):
        p.origin = ORIGINS[data["origem"]]
        changed.append("origem")
    if data.get("licenca_custo_por_unidade") is not None:
        p.license_cost_per_unit = _dec(data["licenca_custo_por_unidade"], "licenca_custo_por_unidade")
        changed.append("licenca_custo_por_unidade")
    if data.get("categoria"):
        p.category = _category(data["categoria"])
        changed.append("categoria")
    return changed


def _set_collections(p: Product, data: dict) -> bool:
    names = data.get("colecoes")
    if names is None:
        return False
    if isinstance(names, str):
        names = [n for n in names.split(",")]
    p.collections.set([Collection.get_or_create_by_name(n) for n in names if str(n).strip()])
    return True


@tool("gerenciar_colecao", "Cria, altera ou exclui uma coleção (linha de design que agrupa peças de categorias diferentes no portal e nos filtros). 'produtos' define a lista de SKUs da coleção.", {
    "acao": {"type": "string", "enum": ["criar", "atualizar", "excluir", "ver"]},
    "colecao": {**S, "description": "nome ou slug (para criar: o nome)"},
    "novo_nome": S, "descricao": S, "mostrar_no_portal": B, "ordem": I,
    "produtos": {"type": "array", "items": S, "description": "SKUs-base; substitui a lista atual"},
    "adicionar_produtos": {"type": "array", "items": S, "description": "SKUs-base para incluir sem tirar os atuais"},
}, ["acao", "colecao"])
def gerenciar_colecao(user, base, acao, colecao, novo_nome="", descricao=None, mostrar_no_portal=None, ordem=None,
                      produtos=None, adicionar_produtos=None):
    if acao == "criar":
        name = " ".join(str(colecao).split())
        if Collection.objects.filter(name__iexact=name).exists():
            raise ToolError(f"Já existe a coleção {name}. Use acao=atualizar.")
        c = Collection(name=name)
    else:
        c = _collection(colecao)
    if acao == "ver":
        return _collection_info(c, base)
    if acao == "excluir":
        info = _collection_info(c, base)
        c.delete()
        return {"excluida": info["nome"], "produtos_desvinculados": info["produtos"]}
    if novo_nome:
        c.name = " ".join(novo_nome.split())
    if descricao is not None:
        c.description = descricao
    if mostrar_no_portal is not None:
        c.show_on_site = bool(mostrar_no_portal)
    if ordem is not None:
        c.order = int(ordem)
    with transaction.atomic():
        c.save()
        if produtos is not None:
            c.products.set([_product(s) for s in produtos])
        if adicionar_produtos:
            c.products.add(*[_product(s) for s in adicionar_produtos])
    return _collection_info(c, base)


@tool("criar_produto", "Cria um produto em rascunho (com uma peça 'Peça única'). SKU-base gerado se omitido (TM-CAT-XXX).", {
    **PRODUCT_PROPS, "sku_base": S,
}, ["nome", "categoria"])
def criar_produto(user, base, nome, categoria, sku_base="", **data):
    cat = _category(categoria)
    sku = (sku_base or "").strip().upper()
    if sku and Product.objects.filter(sku_base=sku).exists():
        raise ToolError(f"Já existe um produto com o SKU-base {sku}.")
    if not sku:
        sku = importer.suggest_sku(nome, cat.code, set(Product.objects.values_list("sku_base", flat=True)))
    with transaction.atomic():
        p = Product(name=nome, category=cat, sku_base=sku, status=Product.Status.DRAFT)
        _apply_product_fields(p, data)
        p.save()
        Part.objects.create(product=p, name="Peça única")
        _set_collections(p, data)
    return {"criado": True, **_product_info(p, base, full=False)}


@tool("atualizar_produto", "Altera dados do produto (textos, medidas, categoria, coleções, origem e licença).", {
    "produto": S, **PRODUCT_PROPS,
}, ["produto"])
def atualizar_produto(user, base, produto, **data):
    p = _product(produto)
    changed = _apply_product_fields(p, data)
    if _set_collections(p, data):
        changed.append("colecoes")
    if not changed:
        raise ToolError("Nada para alterar.")
    p.save()
    if "licenca_custo_por_unidade" in changed:
        workflow.refresh_product(p)
    return {"alterado": changed, **_product_info(p, base, full=False)}


@tool("mudar_status", "Muda o status do produto. 'active' só entra no catálogo se todas as etapas estiverem feitas.", {
    "produto": S, "status": {"type": "string", "enum": ["draft", "active", "seasonal", "discontinued"]},
}, ["produto", "status"])
def mudar_status(user, base, produto, status):
    p = _product(produto)
    p.status = status
    p.save(update_fields=["status", "updated_at"])
    return {"status": p.get_status_display(), "pronto_para_catalogo": p.ready_for_catalog}


# --- escrita: peças --------------------------------------------------------------

@tool("gerenciar_peca", "Cria, renomeia/ajusta ou remove uma peça impressa do produto, ou reaproveita peça de outro produto.", {
    "produto": S,
    "acao": {"type": "string", "enum": ["criar", "alterar", "remover", "compartilhar", "copiar"]},
    "peca_id": {**I, "description": "para alterar/remover, ou a peça de outro produto para compartilhar/copiar"},
    "nome": S, "quantidade": I, "slot_cor": {"type": "string", "enum": ["principal", "secundaria"]},
    "gramas_estimadas": {**N, "description": "custo usa isto enquanto não houver 3MF"},
    "minutos_estimados": I,
    "tamanhos": {**S, "description": "só nestes tamanhos de variante, ex.: 'G' ou 'P, M'; vazio = todos"},
}, ["produto", "acao"])
def gerenciar_peca(user, base, produto, acao, peca_id=None, nome="", quantidade=None, slot_cor="",
                   gramas_estimadas=None, minutos_estimados=None, tamanhos=None):
    p = _product(produto)
    slot = {"principal": Part.ColorSlot.MAIN, "secundaria": Part.ColorSlot.SECONDARY}.get(slot_cor)
    if acao == "criar":
        if not nome:
            raise ToolError("Informe o nome da peça.")
        part = Part.objects.create(product=p, name=nome, default_qty=quantidade or 1, color_slot=slot or Part.ColorSlot.MAIN)
        _apply_part_extras(part, gramas_estimadas, minutos_estimados, tamanhos)
    else:
        part = Part.objects.filter(pk=peca_id).first() if peca_id else None
        if part is None:
            raise ToolError("Informe peca_id (veja ver_produto).")
        if acao == "alterar":
            if nome:
                part.name = nome
            if quantidade:
                part.default_qty = quantidade
                part.bom_items.update(quantity=quantidade)
            if slot:
                part.color_slot = slot
            part.save()
            _apply_part_extras(part, gramas_estimadas, minutos_estimados, tamanhos)
        elif acao == "remover":
            if part.product_id != p.pk:
                p.shared_parts.remove(part)
            else:
                if part.shared_in.exists():
                    raise ToolError("A peça é usada em outros produtos; desvincule lá antes.")
                part.bom_items.all().delete()
                part.delete()
        elif acao == "compartilhar":
            p.shared_parts.add(part)
        elif acao == "copiar":
            src = part
            part = Part.objects.create(product=p, name=src.name, default_qty=src.default_qty, color_slot=src.color_slot)
            for a in src.assets.all():
                Asset.objects.create(product=p, part=part, kind=a.kind, file=a.file.name, name=a.name, version=a.version,
                                     sha256=a.sha256, size_bytes=a.size_bytes, printer=a.printer, is_production=a.is_production,
                                     thumbnail=a.thumbnail.name, grams_total=a.grams_total, print_time_min=a.print_time_min,
                                     meta=a.meta, parsed_at=a.parsed_at, parse_error=a.parse_error)
    workflow.sync_bom_with_parts(p)
    workflow.refresh_product(p)
    return _product_info(p, base)


def _apply_part_extras(part: Part, grams=None, minutes=None, sizes=None) -> None:
    changed = False
    if grams is not None:
        part.est_grams = _dec(grams, "gramas_estimadas") if str(grams).strip() not in ("", "0") else None
        changed = True
    if minutes is not None:
        part.est_minutes = int(minutes) or None
        changed = True
    if sizes is not None:
        part.only_sizes = ", ".join(x.strip().upper() for x in str(sizes).replace(";", ",").split(",") if x.strip())
        changed = True
    if changed:
        part.save()


SLOTS = {"principal": Part.ColorSlot.MAIN, "secundaria": Part.ColorSlot.SECONDARY}


@tool("cadastrar_produto_completo",
      "Cria ou atualiza (sem duplicar) um produto inteiro de uma vez: dados, peças com gramas/tempo estimados "
      "(usados no custo até subir o 3MF) e restrição por tamanho, e variantes com filamentos. Recalcula custo e preços "
      "sugeridos (não aprova). Produto existente: informe 'produto' (SKU); peças casam pelo nome; variantes pelo tamanho. "
      "A peça provisória 'Peça única' sem arquivo é substituída pelas peças informadas.", {
          "produto": {**S, "description": "SKU-base existente; vazio = procura pelo nome exato ou cria"},
          "nome": S, "categoria": S,
          "sku_base": {**S, "description": "só para produto novo (ex.: TM-VAS-ENC); vazio ou já usado = gerado"},
          "origem": {"type": "string", "enum": ["autoral", "licenciado", "sem_licenca"],
                     "description": "só para produto novo; padrão sem_licenca"},
          "descricao_curta": S, "altura_mm": I, "largura_mm": I, "profundidade_mm": I,
          "colecoes": {"type": "array", "items": S, "description": "adiciona às coleções (cria se não existir)"},
          "pecas": {"type": "array", "items": {"type": "object", "properties": {
              "nome": S, "gramas": N, "minutos": I, "quantidade": I,
              "slot": {"type": "string", "enum": ["principal", "secundaria"]},
              "tamanhos": {**S, "description": "ex.: 'G' — só nas variantes desse tamanho"},
          }, "required": ["nome"]}},
          "variantes": {"type": "array", "items": {"type": "object", "properties": {
              "tamanho": S, "filamento_principal": S, "filamento_secundario": S, "cor_principal": S, "cor_secundaria": S,
          }, "required": ["filamento_principal"]}},
      }, ["nome"])
def cadastrar_produto_completo(user, base, nome, produto="", categoria="", origem="", descricao_curta="", sku_base="",
                               altura_mm=None, largura_mm=None, profundidade_mm=None, colecoes=None, pecas=None, variantes=None):
    from apps.catalog.models import Collection

    with transaction.atomic():
        p = _product(produto) if produto else Product.objects.filter(name__iexact=nome.strip()).first()
        created = p is None
        if created:
            if not categoria:
                raise ToolError("Produto novo: informe a categoria.")
            cat = _category(categoria)
            taken = set(Product.objects.values_list("sku_base", flat=True))
            wanted = (sku_base or "").strip().upper()
            p = Product.objects.create(
                name=nome.strip(), category=cat, status=Product.Status.DRAFT,
                sku_base=wanted if wanted and wanted not in taken else importer.suggest_sku(nome, cat.code, taken),
                origin=ORIGINS[origem or "sem_licenca"], short_description=descricao_curta or "",
            )
        elif descricao_curta and not p.short_description:
            p.short_description = descricao_curta
        # medidas: preenche as que faltam (não sobrescreve o que já foi medido)
        for field, value in (("height_mm", altura_mm), ("width_mm", largura_mm), ("depth_mm", profundidade_mm)):
            if value and not getattr(p, field):
                setattr(p, field, int(value))
        p.save()
        if colecoes:
            p.collections.add(*[Collection.get_or_create_by_name(c) for c in colecoes if str(c).strip()])

        log = []
        if pecas:
            own = list(p.parts.all())
            names = {str(x["nome"]).strip().lower() for x in pecas}
            if len(own) == 1 and own[0].name == "Peça única" and "peça única" not in names \
                    and not own[0].production_assets().exists() and not own[0].shared_in.exists():
                moved = own[0].assets.update(part=None)  # STL/3MF de projeto vão para a biblioteca do produto
                own[0].bom_items.all().delete()
                own[0].delete()
                log.append("peça provisória 'Peça única' substituída"
                           + (f" ({moved} arquivo(s) movidos para a biblioteca do produto)" if moved else ""))
            for spec in pecas:
                pname = str(spec["nome"]).strip()[:80]
                part = p.parts.filter(name__iexact=pname).first()
                if part is None:
                    part = Part.objects.create(product=p, name=pname)
                    log.append(f"peça criada: {pname}")
                part.default_qty = int(spec.get("quantidade") or part.default_qty or 1)
                part.color_slot = SLOTS.get(spec.get("slot") or "principal", Part.ColorSlot.MAIN)
                part.save()
                part.bom_items.update(quantity=part.default_qty)
                _apply_part_extras(part, spec.get("gramas"), spec.get("minutos"), spec.get("tamanhos", ""))

        for spec in variantes or []:
            size = (spec.get("tamanho") or "Único").strip()
            v = p.variants.filter(size__iexact=size).first()
            color = _color(spec.get("cor_principal"))
            if v is None:
                v = Variant.objects.create(
                    product=p, sku=workflow.suggest_variant_sku(p, size, color), size=size, primary_color=color,
                    is_default=not p.variants.exists(),
                )
                log.append(f"variante criada: {v.sku}")
            elif color:
                v.primary_color = color
            v.main_filament = _filament(spec["filamento_principal"])
            if spec.get("filamento_secundario"):
                v.secondary_filament = _filament(spec["filamento_secundario"])
            if spec.get("cor_secundaria"):
                v.secondary_color = _color(spec["cor_secundaria"])
            v.save()
        workflow.sync_bom_with_parts(p)
        for v in p.variants.all():
            workflow.refresh_variant(v)

    site = []
    for v in p.variants.all():
        rows = {r["canal"]: r["sugerido"] for r in _price_rows(v)}
        site.append({"sku": v.sku, "custo": str(v.cost_cached) if v.cost_cached is not None else None,
                     "composicao": [f"{b.quantity.normalize():f} × {b.part.name}" for b in v.bom_items.select_related("part") if b.part],
                     "sugerido": rows,
                     "avisos": [m for m in (v.cost_breakdown or {}).get("problemas", []) if "estimativa manual" not in m]})
    return {"criado": created, "sku_base": p.sku_base, "nome": p.name, "alteracoes": log, "variantes": site,
            "ficha": _url(base, "catalog:product_detail", p.pk)}


@tool("definir_placas",
      "Para um 3MF fatiado com várias placas: diz se as placas são TAMANHOS diferentes do mesmo objeto (cada placa vira "
      "uma peça com o custo dela e cada tamanho uma variante) ou PARTES do mesmo objeto (custo somado na mesma peça). "
      "Pergunte ao usuário antes se não estiver claro (nomes como '100%'/'70%' indicam tamanhos).", {
          "produto": S, "peca_id": I,
          "modo": {"type": "string", "enum": ["tamanhos", "partes"]},
          "tamanhos": {"type": "array", "items": S, "description": "um por placa, na ordem (ex.: ['G','P']); padrão = nome das placas"},
          "filamento_principal": {**S, "description": "obrigatório em 'tamanhos' se o produto ainda não tem variante"},
      }, ["produto", "peca_id", "modo"])
def definir_placas(user, base, produto, peca_id, modo, tamanhos=None, filamento_principal=""):
    from apps.catalog.views import _refresh_plate_names

    p = _product(produto)
    part = next((x for x in p.all_parts() if x.pk == int(peca_id)), None)
    if part is None:
        raise ToolError("Peça não encontrada neste produto (veja ver_produto).")
    asset = part.production_assets().first()
    if asset is not None:
        _refresh_plate_names(asset)
    try:
        log = workflow.apply_plates(part, {"tamanhos": Asset.PLATES_SIZES, "partes": Asset.PLATES_PARTS}[modo],
                                    tamanhos, _filament(filamento_principal))
    except workflow.PlatesError as exc:
        raise ToolError(str(exc))
    return {"feito": log, **_product_info(p, base)}


# --- escrita: variantes e preço -------------------------------------------------

@tool("criar_variante", "Cria variante (tamanho/cor) com SKU automático, composição com as peças do produto, e já calcula custo e preços sugeridos.", {
    "produto": S, "tamanho": {**S, "description": "Único, P, M, G..."},
    "cor_principal": {**S, "description": "código ou nome da cor"}, "filamento_principal": {**S, "description": "id ou busca (marca material cor)"},
    "cor_secundaria": S, "filamento_secundario": S,
}, ["produto", "filamento_principal"])
def criar_variante(user, base, produto, filamento_principal, tamanho="Único", cor_principal="", cor_secundaria="", filamento_secundario=""):
    p = _product(produto)
    color = _color(cor_principal)
    v = Variant.objects.create(
        product=p, sku=workflow.suggest_variant_sku(p, tamanho, color), size=tamanho, primary_color=color,
        secondary_color=_color(cor_secundaria), main_filament=_filament(filamento_principal),
        secondary_filament=_filament(filamento_secundario), is_default=not p.variants.exists(),
    )
    workflow.refresh_variant(v)
    return _variant_info(v)


@tool("atualizar_variante", "Altera variante: tamanho, cores, filamentos, minutos de mão de obra, estoque pronto, quantidade de peças e componentes (kit, embalagem). Recalcula custo.", {
    "variante": {**S, "description": "SKU da variante"}, "tamanho": S, "cor_principal": S, "cor_secundaria": S,
    "filamento_principal": S, "filamento_secundario": S, "minutos_pos": I, "minutos_montagem": I, "minutos_embalagem": I,
    "estoque_pronto": I, "padrao": B,
    "adicionar_componente": {"type": "object", "properties": {"componente": S, "quantidade": N}, "required": ["componente"]},
    "remover_componente": S,
    "quantidade_peca": {"type": "object", "properties": {"peca_id": I, "quantidade": N}, "required": ["peca_id", "quantidade"]},
}, ["variante"])
def atualizar_variante(user, base, variante, **d):
    v = _variant(variante)
    simple = {"tamanho": "size", "minutos_pos": "minutes_post", "minutos_montagem": "minutes_assembly",
              "minutos_embalagem": "minutes_packaging", "estoque_pronto": "stock_ready"}
    for k, f in simple.items():
        if d.get(k) is not None:
            setattr(v, f, d[k])
    if d.get("cor_principal"):
        v.primary_color = _color(d["cor_principal"])
    if d.get("cor_secundaria"):
        v.secondary_color = _color(d["cor_secundaria"])
    if d.get("filamento_principal"):
        v.main_filament = _filament(d["filamento_principal"])
    if d.get("filamento_secundario"):
        v.secondary_filament = _filament(d["filamento_secundario"])
    if d.get("padrao"):
        v.product.variants.exclude(pk=v.pk).update(is_default=False)
        v.is_default = True
    v.save()
    if d.get("adicionar_componente"):
        ref = str(d["adicionar_componente"]["componente"])
        comp = Component.objects.filter(pk=ref).first() if ref.isdigit() else Component.objects.filter(name__icontains=ref).first()
        if comp is None:
            raise ToolError(f"Componente não encontrado: {ref}. Use criar_componente.")
        BOMItem.objects.create(variant=v, kind=BOMItem.Kind.COMPONENT, component=comp,
                               quantity=_dec(d["adicionar_componente"].get("quantidade") or 1, "quantidade"))
    if d.get("remover_componente"):
        ref = str(d["remover_componente"])
        v.bom_items.filter(kind=BOMItem.Kind.COMPONENT).filter(
            Q(component__pk=ref) if ref.isdigit() else Q(component__name__icontains=ref)).delete()
    if d.get("quantidade_peca"):
        v.bom_items.filter(kind=BOMItem.Kind.PART, part_id=d["quantidade_peca"]["peca_id"]).update(
            quantity=_dec(d["quantidade_peca"]["quantidade"], "quantidade"))
    workflow.refresh_variant(v)
    return _variant_info(v)


@tool("excluir_variante", "Exclui uma variante (e os preços dela). Não exclui se houver ordens de produção.", {"variante": S}, ["variante"])
def excluir_variante(user, base, variante):
    v = _variant(variante)
    if v.orders.exists():
        raise ToolError(f"{v.sku} tem ordens de produção e não pode ser excluída.")
    product, was_default, sku = v.product, v.is_default, v.sku
    v.delete()
    if was_default and product.variants.exists():
        product.variants.order_by("created_at").update(is_default=False)
        first = product.variants.order_by("created_at").first()
        first.is_default = True
        first.save(update_fields=["is_default"])
    return {"excluida": sku}


@tool("aprovar_precos", "Aprova preços da variante. Sem 'precos' aprova o sugerido em todos os canais; com 'precos' aprova só os canais informados com o valor dado (ou 'sugerido').", {
    "variante": S,
    "precos": {"type": "object", "description": "{\"Site\": 39.9, \"Mercado Livre\": \"sugerido\"}", "additionalProperties": {"type": ["number", "string"]}},
}, ["variante"])
def aprovar_precos(user, base, variante, precos=None):
    v = _variant(variante)
    rows = {r.channel.name.lower(): r for r in latest_prices(v)}
    if not rows:
        raise ToolError("Variante sem preços calculados (falta custo ou canal ativo).")
    targets = precos or {name: "sugerido" for name in rows}
    done, warns = [], []
    for name, value in targets.items():
        row = rows.get(str(name).lower())
        if row is None:
            raise ToolError(f"Canal desconhecido: {name}. Canais: {', '.join(r.channel.name for r in rows.values())}")
        price = row.suggested_price if str(value).lower() == "sugerido" else _dec(value, f"preço {name}")
        new = approve_price(row, user, price=price)
        done.append(f"{new.channel.name} R$ {new.current_price} (margem {new.margin_pct}%)")
        if new.below_minimum:
            warns.append(f"{new.channel.name}: margem abaixo do mínimo")
    return {"aprovados": done, "avisos": warns, "precos": _price_rows(v)}


# --- estoque e custo ------------------------------------------------------------

@tool("criar_filamento", "Cadastra um filamento (marca, material, cor). O custo vem depois, com registrar_compra.", {
    "marca": S, "material": {**S, "description": "PLA, PLA Silk, PETG..."}, "cor": S, "fabricante": S, "hex": S,
    "cor_sku": {**S, "description": "código da cor no SKU (BRC...)"}, "estoque_minimo_g": N,
}, ["marca", "material", "cor"])
def criar_filamento(user, base, marca, material, cor, fabricante="", hex="", cor_sku="", estoque_minimo_g=None):
    f, created = FilamentSKU.objects.get_or_create(brand=marca, material=material, color_name=cor, defaults={
        "manufacturer": fabricante or marca, "color_hex": hex, "color": _color(cor_sku) if cor_sku else None,
    })
    if estoque_minimo_g is not None:
        f.min_stock_g = _dec(estoque_minimo_g, "estoque_minimo_g")
        f.save(update_fields=["min_stock_g"])
    return {"id": f.pk, "nome": str(f), "criado": created}


@tool("registrar_compra", "Lança compra de filamento (gramas e valor) ou de componente (quantidade e valor). Atualiza estoque, custo médio e recalcula os produtos afetados.", {
    "filamento": {**S, "description": "id ou busca"}, "componente": {**S, "description": "id ou nome"},
    "quantidade": {**N, "description": "rolos ou unidades"}, "gramas_total": N, "valor_itens": N, "frete": N,
    "fornecedor": S, "nota": S,
}, ["valor_itens"])
def registrar_compra(user, base, valor_itens, filamento="", componente="", quantidade=1, gramas_total=None, frete=0, fornecedor="", nota=""):
    fil = _filament(filamento) if filamento else None
    comp = None
    if componente:
        comp = Component.objects.filter(pk=componente).first() if str(componente).isdigit() else Component.objects.filter(name__icontains=componente).first()
        if comp is None:
            raise ToolError(f"Componente não encontrado: {componente}")
    if bool(fil) == bool(comp):
        raise ToolError("Informe filamento ou componente (um dos dois).")
    if fil and not gramas_total:
        raise ToolError("Para filamento, informe gramas_total.")
    p = Purchase(filament=fil, component=comp, quantity=_dec(quantidade, "quantidade"),
                 grams_total=_dec(gramas_total, "gramas_total") if gramas_total else None,
                 total_cost=_dec(valor_itens, "valor_itens"), shipping_cost=_dec(frete or 0, "frete"),
                 supplier=fornecedor, invoice_ref=nota, created_by=user)
    p.full_clean()
    p.save()
    p.apply()
    if fil:
        fil.refresh_from_db()
        return {"filamento": str(fil), "custo_kg": str(fil.avg_cost_per_kg), "saldo_g": str(fil.stock_g)}
    comp.refresh_from_db()
    return {"componente": comp.name, "custo_unitario": str(comp.unit_cost), "saldo": str(comp.stock_qty)}


@tool("criar_componente", "Cadastra componente comprado (kit elétrico, lâmpada, caixa, tag...).", {
    "nome": S, "custo_unitario": N, "embalagem": B, "fornecedor": S,
}, ["nome", "custo_unitario"])
def criar_componente(user, base, nome, custo_unitario, embalagem=False, fornecedor=""):
    c = Component.objects.create(name=nome, unit_cost=_dec(custo_unitario, "custo_unitario"), is_packaging=embalagem, supplier=fornecedor)
    return {"id": c.pk, "nome": c.name}


# --- arquivos ------------------------------------------------------------------

def _download(url: str, dest: Path) -> Path:
    import requests

    headers = {"User-Agent": "Toomuch-MCP"}
    u = urlparse(url)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("TOOMUCH_GITHUB_TOKEN")
    m = re.match(r"^/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$", u.path) if u.hostname == "github.com" else None
    if m:  # github.com/dono/repo/blob/ref/caminho → API (funciona em repositório privado com token)
        owner, repo, ref, path = m.groups()
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={ref}"
        headers["Accept"] = "application/vnd.github.raw"
    if token and (u.hostname or "").endswith(("github.com", "githubusercontent.com")):
        headers["Authorization"] = f"Bearer {token}"
    with requests.get(url, headers=headers, stream=True, timeout=60, allow_redirects=True) as r:
        if r.status_code >= 400:
            raise ToolError(f"Download falhou ({r.status_code}). Link público? Para GitHub privado, configure GITHUB_TOKEN no servidor.")
        total = 0
        with open(dest, "wb") as out:
            for chunk in r.iter_content(1 << 20):
                total += len(chunk)
                if total > MAX_DOWNLOAD_MB * 1024 * 1024:
                    raise ToolError(f"Arquivo maior que {MAX_DOWNLOAD_MB} MB.")
                out.write(chunk)
    return dest


@tool("importar_pacote_url", "Baixa um ZIP de importação em lote (uma pasta por produto, produtos.csv opcional) de um link e importa tudo. Idempotente: arquivos repetidos são ignorados. Link do GitHub privado funciona se o servidor tiver GITHUB_TOKEN.", {
    "url": S,
}, ["url"])
def importar_pacote_url(user, base, url):
    tmp = Path(tempfile.mkdtemp(prefix="mcp-import-"))
    try:
        z = _download(url, tmp / "pacote.zip")
        work = tmp / "x"
        work.mkdir()
        with zipfile.ZipFile(z) as zf:
            from apps.catalog.views_import import _flatten_single_root, _safe_extract

            _safe_extract(zf, work)
        _flatten_single_root(work)
        report = importer.apply(work, user=user)
    except zipfile.BadZipFile:
        raise ToolError("O arquivo baixado não é um ZIP.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"produtos_criados": report.created_products, "produtos_reaproveitados": report.reused_products,
            "arquivos_novos": report.files_added, "arquivos_repetidos": report.files_skipped, "erros": report.errors}


@tool("enviar_arquivo_url", "Baixa um arquivo de um link e anexa: 3MF fatiado (.gcode.3mf) a uma peça (vira o de produção e recalcula o custo) ou foto ao produto.", {
    "produto": S, "url": S, "tipo": {"type": "string", "enum": ["3mf_fatiado", "foto"]},
    "peca_id": {**I, "description": "obrigatório para 3mf_fatiado"}, "nome_arquivo": S, "foto_principal": B,
}, ["produto", "url", "tipo"])
def enviar_arquivo_url(user, base, produto, url, tipo, peca_id=None, nome_arquivo="", foto_principal=False):
    p = _product(produto)
    name = nome_arquivo or Path(urlparse(url).path).name or "arquivo"
    tmp = Path(tempfile.mkdtemp(prefix="mcp-file-"))
    try:
        path = _download(url, tmp / name)
        if tipo == "foto":
            if foto_principal:
                p.assets.filter(is_main=True).update(is_main=False)
            has_main = p.assets.filter(kind=Asset.Kind.PHOTO, is_main=True).exists()
            a = Asset(product=p, kind=Asset.Kind.PHOTO, uploaded_by=user, is_main=foto_principal or not has_main)
            with open(path, "rb") as fh:
                a.file.save(name, File(fh), save=True)
            return {"foto_id": a.pk, "principal": a.is_main}
        part = Part.objects.filter(pk=peca_id).first() if peca_id else None
        if part is None or part not in p.all_parts():
            raise ToolError("Informe peca_id de uma peça deste produto (veja ver_produto).")
        old = list(part.assets.filter(kind=Asset.Kind.THREEMF_SLICED, is_production=True).values_list("pk", flat=True))
        version = (part.assets.filter(kind=Asset.Kind.THREEMF_SLICED).order_by("-version").values_list("version", flat=True).first() or 0) + 1
        a = Asset(product=part.product, part=part, kind=Asset.Kind.THREEMF_SLICED, is_production=True, version=version, uploaded_by=user)
        with open(path, "rb") as fh:
            a.file.save(name, File(fh), save=True)
        a.refresh_from_db()
        if a.parse_error:
            a.is_production = False
            a.save(update_fields=["is_production"])
            raise ToolError(f"Não é um 3MF fatiado: {a.parse_error}")
        Asset.objects.filter(pk__in=old).update(is_production=False)
        workflow.refresh_product(part.product)
        for other in part.shared_in.all():
            workflow.refresh_product(other)
        return {"arquivo_id": a.pk, "gramas": str(a.grams_total), "minutos": a.print_time_min, **_product_info(p, base, full=False)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
