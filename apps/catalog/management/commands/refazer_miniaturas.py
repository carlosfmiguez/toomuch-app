"""Gera a miniatura das fotos que ficaram sem (ex.: o servidor caiu no meio do envio). Roda a cada deploy."""

from django.core.management.base import BaseCommand

from apps.catalog.models import Asset
from apps.catalog.services import parse_asset


class Command(BaseCommand):
    help = "Gera miniaturas faltantes das fotos."

    def handle(self, *args, **opts):
        done = failed = 0
        for asset in Asset.objects.filter(kind__in=[Asset.Kind.PHOTO, Asset.Kind.RENDER], thumbnail="").exclude(file=""):
            try:
                if not asset.file.storage.exists(asset.file.name):
                    self.stdout.write(f"  sem arquivo: asset {asset.pk} ({asset.file.name})")
                    failed += 1
                    continue
                parse_asset(asset)
                done += 1 if asset.thumbnail else 0
            except Exception as exc:  # noqa: BLE001 - nunca travar o deploy
                failed += 1
                self.stdout.write(f"  falhou asset {asset.pk}: {exc}")
        self.stdout.write(f"miniaturas: {done} geradas, {failed} com problema")
