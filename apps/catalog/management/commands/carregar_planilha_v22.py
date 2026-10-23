"""
Carga única da planilha antiga 'Fórmula da Precificação 3D V2.2' (outubro/2026).

Cada item do JSON é passado para a mesma rotina do conector (cadastrar_produto_completo):
atualiza os produtos que já existem (SKU), cria os que faltam (sem licença, rascunho), cadastra as
peças com gramas/tempo estimados e as variantes com os filamentos. Não aprova preço.

Os filamentos são referenciados pelo id que receberam no sistema de produção; em outro banco
(teste, instalação nova) a carga é pulada.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand

DATA = Path(__file__).resolve().parents[2] / "data" / "planilha_precificacao_v22.json"
# id → (marca, trecho do material) dos filamentos usados na carga, como estão em produção
FINGERPRINT = {1: ("Bambulab", "LITE"), 4: ("3DLAB", "PLA"), 5: ("3DFila", "Fosco"), 6: ("Creality", "Soleyn"),
               8: ("Sunlu", "Básico"), 9: ("Creality", "Wood"), 10: ("Elegoo", "Wood"), 15: ("Bambulab", "Basic"),
               22: ("Sunlu", "Silk"), 23: ("STLFLIX", "Matte"), 24: ("Creality", "PETG"), 25: ("Bambulab", "PETG")}


def database_matches() -> bool:
    from apps.inventory.models import FilamentSKU

    found = {f.pk: f for f in FilamentSKU.objects.filter(pk__in=FINGERPRINT)}
    return all(pk in found and found[pk].brand == brand and part in found[pk].material for pk, (brand, part) in FINGERPRINT.items())


class Command(BaseCommand):
    help = "Carrega os produtos da planilha de precificação V2.2 (peças com estimativa e variantes)."

    def add_arguments(self, parser):
        parser.add_argument("--forcar", action="store_true", help="roda mesmo se os filamentos não baterem")

    def handle(self, *args, forcar=False, **opts):
        from django.contrib.auth import get_user_model

        from apps.connector.tools import ToolError, cadastrar_produto_completo

        if not forcar and not database_matches():
            self.stdout.write("[planilha v2.2] filamentos de produção não encontrados neste banco: carga pulada.")
            return
        user = get_user_model().objects.filter(is_superuser=True).order_by("pk").first()
        items = json.loads(DATA.read_text(encoding="utf-8"))
        created = updated = 0
        for spec in items:
            try:
                out = cadastrar_produto_completo(user, "", **spec)
            except (ToolError, Exception) as exc:  # noqa: BLE001 - um item ruim não para a carga
                self.stdout.write(f"[planilha v2.2] ERRO {spec.get('nome')}: {exc}")
                continue
            created += out["criado"]
            updated += not out["criado"]
            custos = ", ".join(f"{v['sku']} R$ {v['custo']}" for v in out["variantes"])
            self.stdout.write(f"[planilha v2.2] {'novo' if out['criado'] else 'atualizado'} {out['sku_base']} · {custos}")
        self.stdout.write(f"[planilha v2.2] fim: {created} criados, {updated} atualizados.")
