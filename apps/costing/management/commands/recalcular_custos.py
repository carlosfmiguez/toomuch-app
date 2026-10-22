from django.core.management.base import BaseCommand

from apps.costing.services import recompute_all


class Command(BaseCommand):
    help = "Recalcula custo e preço sugerido de todas as variantes (mantém os preços aprovados)."

    def handle(self, *args, **options):
        n = recompute_all()
        self.stdout.write(f"{n} variante(s) recalculada(s).")
