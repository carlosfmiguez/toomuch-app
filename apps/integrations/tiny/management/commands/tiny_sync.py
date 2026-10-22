"""
Sincronização com o Tiny para agendar na hospedagem (cron):

    python manage.py tiny_sync            # pedidos → ordens e produtos → Tiny
    python manage.py tiny_sync --so-pedidos
    python manage.py tiny_sync --so-produtos --base-url https://app.toomuch.art
"""

from django.core.management.base import BaseCommand

from apps.integrations.tiny import services
from apps.integrations.tiny.client import TinyAuthError, TinyClient
from apps.integrations.tiny.models import TinyConnection


class Command(BaseCommand):
    help = "Sincroniza pedidos (Tiny → ordens de produção) e produtos (→ Tiny)."

    def add_arguments(self, parser):
        parser.add_argument("--so-pedidos", action="store_true")
        parser.add_argument("--so-produtos", action="store_true")
        parser.add_argument("--base-url", default="", help="URL pública do sistema, para os links das fotos")
        parser.add_argument("--forcar", action="store_true", help="reenvia todos os produtos mesmo sem mudança")

    def handle(self, *args, **options):
        conn = TinyConnection.get()
        if not conn.connected:
            self.stdout.write(self.style.WARNING("Tiny não conectado; abra /integracoes/tiny/ e conecte."))
            return
        client = TinyClient(conn)
        try:
            if not options["so_produtos"] and conn.order_sync_enabled:
                r = services.sync_orders(client)
                self.stdout.write(f"Pedidos: {r.seen} vistos, {r.created_orders} ordens criadas, {r.skipped} sem itens conhecidos, {len(r.errors)} erros")
                for e in r.errors:
                    self.stdout.write(self.style.WARNING(f"  {e}"))
            if not options["so_pedidos"] and conn.product_push_enabled:
                r = services.push_all(client, base_url=options["base_url"], force=options["forcar"])
                self.stdout.write(f"Produtos: {r.created} criados, {r.updated} atualizados, {r.unchanged} iguais, {len(r.skipped)} pulados, {len(r.errors)} erros")
                for e in r.errors:
                    self.stdout.write(self.style.WARNING(f"  {e}"))
        except TinyAuthError as exc:
            self.stdout.write(self.style.ERROR(f"Autenticação: {exc}"))
