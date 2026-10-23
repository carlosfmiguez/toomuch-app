"""
Carga única dos modelos do designer simiu baixados do Thangs (pasta Downloads/simiu, outubro/2026).

- Produto novo: rascunho, origem "sem licença" (licença a confirmar), autor "simiu (Thangs)" com o link
  da página, descrição curta, medidas informadas pelo designer, coleção da linha de design e a peça
  provisória "Peça única" (o 3MF vem depois).
- Produto que já existia (THALOR, STACKSEY, KUTAS, TISSAIR, TOPO...): só completa o que está vazio,
  normaliza o autor que veio do MakerWorld como "designer id 2598485249" e entra na coleção.
As fotos chegam pela importação em lote (ZIP com uma pasta por SKU).
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

DATA = Path(__file__).resolve().parents[2] / "data" / "simiu_thangs.json"
AUTHOR = "simiu (Thangs)"
TERMS = "Thangs: confirmar licença comercial com o designer antes de vender"


def database_matches() -> bool:
    from apps.catalog.models import Product

    return Product.objects.filter(sku_base__in=["TM-MAQ-TAY", "TM-VAS-TAY", "TM-BAN-DRW", "TM-CAS-RTP"]).count() == 4


class Command(BaseCommand):
    help = "Carrega os produtos do designer simiu (Thangs): cria os novos e completa os existentes."

    def add_arguments(self, parser):
        parser.add_argument("--forcar", action="store_true")

    def handle(self, *args, forcar=False, **opts):
        from apps.catalog.models import Category, Collection, Part, Product

        if not forcar and not database_matches():
            self.stdout.write("[simiu] produtos de produção não encontrados neste banco: carga pulada.")
            return
        cats = {c.code: c for c in Category.objects.all()}
        created = updated = 0
        for it in json.loads(DATA.read_text(encoding="utf-8")):
            try:
                with transaction.atomic():
                    for sku in it["skus"]:
                        p = Product.objects.filter(sku_base=sku).first()
                        if p is None:
                            if not it["novo"]:
                                self.stdout.write(f"[simiu] {sku} esperado mas não existe: pulado")
                                continue
                            p = Product.objects.create(
                                sku_base=sku, name=it["nome"][:120], category=cats.get(it["categoria"]) or cats["UTI"],
                                status=Product.Status.DRAFT, origin=Product.Origin.UNLICENSED,
                                short_description=it["descricao_curta"][:200], license_author=AUTHOR,
                                license_url=it["link"][:200], license_terms=TERMS,
                            )
                            Part.objects.create(product=p, name="Peça única")
                            created += 1
                        else:
                            changed = []
                            if not p.short_description and it["descricao_curta"]:
                                p.short_description = it["descricao_curta"][:200]; changed.append("short_description")
                            if not p.license_author or "2598485249" in p.license_author:
                                p.license_author = AUTHOR; changed.append("license_author")
                            if not p.license_url and it["link"]:
                                p.license_url = it["link"][:200]; changed.append("license_url")
                            if changed:
                                p.save(update_fields=changed + ["updated_at"])
                            updated += 1
                        for field, key in (("height_mm", "altura_mm"), ("width_mm", "largura_mm"), ("depth_mm", "profundidade_mm")):
                            if it.get(key) and not getattr(p, field):
                                setattr(p, field, int(it[key]))
                                p.save(update_fields=[field, "updated_at"])
                        for name in it.get("colecoes") or []:
                            p.collections.add(Collection.get_or_create_by_name(name))
            except Exception as exc:  # noqa: BLE001 - um item ruim não para a carga
                self.stdout.write(f"[simiu] ERRO {it.get('nome')}: {exc}")
        self.stdout.write(f"[simiu] fim: {created} criados, {updated} atualizados.")
